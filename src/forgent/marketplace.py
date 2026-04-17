"""Marketplace stub: `forgent install <name-or-url>`.

Pulls a curated catalog entry (or a full third-party pack repo) into the
local registry, making it available to `forgent advise` on the next call.

Scaffold quality: this ships the functional path -- clone, validate, vendor
into the registry -- but does NOT yet include:
  - Cryptographic signing / revocation (deferred; see ROADMAP.md)
  - Usage telemetry opt-in (deferred; no backend yet)
  - Remote version pinning

The community registry is currently a hardcoded dict of known-good repos.
Over time this should move to a real catalog endpoint.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from forgent.registry.loader import PKG_DIR


# Curated list of community packs. Names map to GitHub URLs we trust enough
# to vendor with one command. Users can always `forgent install <url>` to
# pull anything else.
KNOWN_PACKS: dict[str, str] = {
    "wshobson-agents": "https://github.com/wshobson/agents",
    "voltagent": "https://github.com/VoltAgent/awesome-claude-code-subagents",
    "furai": "https://github.com/0xfurai/claude-code-subagents",
}


@dataclass
class InstallResult:
    pack_name: str
    source_url: str
    agents_added: int
    destination: Path


def install(name_or_url: str) -> InstallResult:
    """Install a pack from a known name OR a git URL.

    Workflow:
      1. Resolve name_or_url to a git URL (known packs or a literal URL).
      2. Shallow-clone into a temp dir.
      3. Look for a top-level `catalog.yaml` or a directory of `.md` agents.
      4. Copy each .md with YAML frontmatter into
         registry/agents/claude_code/<pack-name>/ and append entries to
         dynamic.yaml so they show up on next registry load.
    """
    url = KNOWN_PACKS.get(name_or_url, name_or_url)
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("git@")):
        raise ValueError(
            f"'{name_or_url}' isn't a known pack and doesn't look like a git URL. "
            f"Known packs: {', '.join(KNOWN_PACKS)}"
        )
    pack_name = _slugify(name_or_url if name_or_url != url else _repo_name(url))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "pack"
        _run(["git", "clone", "--depth=1", url, str(tmp_path)])
        added = _vendor_pack(tmp_path, pack_name)
        dest = PKG_DIR / "agents" / "claude_code" / pack_name
    return InstallResult(pack_name=pack_name, source_url=url, agents_added=added, destination=dest)


def _run(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{res.stderr}")
    return res.stdout


def _repo_name(url: str) -> str:
    base = url.rstrip("/").rsplit("/", 1)[-1]
    return base.removesuffix(".git")


def _slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s).strip("-").lower()


def _vendor_pack(src: Path, pack_name: str) -> int:
    """Copy agent .md files from src into the registry, return count added."""
    dest_dir = PKG_DIR / "agents" / "claude_code" / pack_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Find all .md files with frontmatter (the convention for Claude Code
    # subagents). Flatten into a single dir per pack.
    md_files = list(src.rglob("*.md"))
    added = 0
    dynamic_entries: list[dict] = []
    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not text.startswith("---\n"):
            continue
        try:
            _, frontmatter_raw, body = text.split("---\n", 2)
        except ValueError:
            continue
        try:
            fm = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError:
            continue
        name = fm.get("name") or md.stem
        name_slug = f"{pack_name}-{_slugify(str(name))}"
        dst = dest_dir / f"{name_slug}.md"
        dst.write_text(text, encoding="utf-8")
        dynamic_entries.append({
            "name": name_slug,
            "ecosystem": "claude_code",
            "category": fm.get("category", "community"),
            "description": str(fm.get("description") or f"Installed from {pack_name}")[:240],
            "capabilities": _parse_caps(fm),
            "source_repo": pack_name,
            "source_path": str(md.relative_to(src)),
            "model": fm.get("model", "sonnet"),
            "kind": "agent",
            "installed": True,
        })
        added += 1
    _merge_dynamic_yaml(dynamic_entries)
    return added


def _parse_caps(fm: dict) -> list[str]:
    caps = fm.get("capabilities")
    if isinstance(caps, list):
        return [str(c) for c in caps][:8]
    # Some catalogs use a comma-separated string in `tools` or `tags`.
    for alt in ("tools", "tags"):
        raw = fm.get(alt)
        if isinstance(raw, str):
            return [t.strip() for t in raw.split(",") if t.strip()][:8]
        if isinstance(raw, list):
            return [str(t) for t in raw][:8]
    return []


def _merge_dynamic_yaml(entries: list[dict]) -> None:
    if not entries:
        return
    path = PKG_DIR / "dynamic.yaml"
    existing: list[dict] = []
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            existing = data.get("agents") or []
        except yaml.YAMLError:
            existing = []
    known = {e.get("name") for e in existing if isinstance(e, dict)}
    for entry in entries:
        if entry["name"] not in known:
            existing.append(entry)
    path.write_text(
        yaml.safe_dump({"agents": existing}, sort_keys=False),
        encoding="utf-8",
    )
