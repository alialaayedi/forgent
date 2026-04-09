"""Registry loader.

Reads the curated catalog.yaml, optionally vendors source files into
registry/agents/, and exposes an in-memory `Registry` the rest of the
orchestrator queries.

The registry is the *only* source of truth for what agents exist. Adapters
look up agents here, the router scores against this list, the CLI lists
from this. Nothing else should walk `sources/` at runtime.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml

# Project root → resolves whether we're installed or run from source.
PKG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_DIR.parents[2]
CATALOG_PATH = PKG_DIR / "catalog.yaml"
DYNAMIC_CATALOG_PATH = PKG_DIR / "dynamic.yaml"
VENDORED_DIR = PKG_DIR / "agents"
SOURCES_DIR = PROJECT_ROOT / "sources"


class Ecosystem(str, Enum):
    CLAUDE_CODE = "claude_code"
    PYTHON_FRAMEWORK = "python_framework"
    MCP = "mcp"


@dataclass
class AgentSpec:
    """One curated agent — stable shape the rest of the system depends on."""

    name: str
    ecosystem: Ecosystem
    category: str
    description: str
    capabilities: list[str]
    source_repo: str
    source_path: str
    model: str | None = None
    kind: str = "agent"                # "agent" | "workflow" | "tool"
    server_command: str | None = None  # MCP only
    system_prompt: str = ""             # populated lazily from the vendored .md
    frontmatter: dict[str, Any] = field(default_factory=dict)

    @property
    def vendored_path(self) -> Path:
        return VENDORED_DIR / self.ecosystem.value / f"{self.name}.md"

    def load_body(self) -> str:
        """Read the agent's system prompt — vendored copy preferred, sources fallback."""
        if self.system_prompt:
            return self.system_prompt
        path = self.vendored_path
        if not path.exists():
            # Fall back to sources/ if curation hasn't been vendored yet
            fallback = SOURCES_DIR / self.source_repo / self.source_path
            if fallback.exists() and fallback.is_file():
                path = fallback
            else:
                return ""
        text = path.read_text(encoding="utf-8")
        body, fm = _split_frontmatter(text)
        self.system_prompt = body
        self.frontmatter = fm
        return body

    def matches(self, query: str) -> int:
        """Cheap relevance score — used as a fallback when there's no LLM router."""
        q = query.lower()
        score = 0
        if self.name.lower() in q:
            score += 10
        for cap in self.capabilities:
            if cap.lower() in q:
                score += 3
        if any(word in q for word in self.description.lower().split()):
            score += 1
        return score


class Registry:
    """In-memory registry loaded from catalog.yaml."""

    def __init__(self, agents: list[AgentSpec]):
        self.agents = agents
        self._by_name = {a.name: a for a in agents}

    @classmethod
    def load(
        cls,
        catalog_path: Path | str = CATALOG_PATH,
        include_dynamic: bool = True,
    ) -> "Registry":
        """Load curated agents and (by default) any forged dynamic agents.

        The dynamic catalog is appended after the static one so dynamic
        agents with the same name override curated entries.
        """
        path = Path(catalog_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_agents = list(data.get("agents", []))

        if include_dynamic and DYNAMIC_CATALOG_PATH.exists():
            dyn = yaml.safe_load(DYNAMIC_CATALOG_PATH.read_text(encoding="utf-8")) or {}
            raw_agents.extend(dyn.get("agents", []))

        # Dedup by name — last one wins so dynamic overrides static.
        by_name: dict[str, dict] = {}
        for d in raw_agents:
            by_name[d["name"]] = d
        agents = [_spec_from_dict(d) for d in by_name.values()]
        return cls(agents)

    def get(self, name: str) -> AgentSpec | None:
        return self._by_name.get(name)

    def filter(
        self,
        ecosystem: Ecosystem | None = None,
        category: str | None = None,
        capability: str | None = None,
    ) -> list[AgentSpec]:
        out = self.agents
        if ecosystem is not None:
            out = [a for a in out if a.ecosystem == ecosystem]
        if category is not None:
            out = [a for a in out if a.category == category]
        if capability is not None:
            out = [a for a in out if capability in a.capabilities]
        return out

    def search(self, query: str, limit: int = 10) -> list[AgentSpec]:
        scored = [(a, a.matches(query)) for a in self.agents]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        return [s[0] for s in scored[:limit]]

    def categories(self) -> list[str]:
        return sorted({a.category for a in self.agents})

    def __len__(self) -> int:
        return len(self.agents)

    def __iter__(self) -> Iterable[AgentSpec]:
        return iter(self.agents)

    def vendor(self, force: bool = False) -> tuple[int, int]:
        """Copy each catalog entry's source file into registry/agents/.

        Makes the project self-contained — `sources/` can be deleted afterwards.
        Returns (copied, skipped).
        """
        copied = skipped = 0
        for agent in self.agents:
            if agent.ecosystem == Ecosystem.MCP:
                # MCP agents don't have a markdown body — they're tool servers
                skipped += 1
                continue
            src = SOURCES_DIR / agent.source_repo / agent.source_path
            if not src.exists():
                skipped += 1
                continue
            if src.is_dir():
                # Python framework workflows are directories — vendor a manifest
                manifest = agent.vendored_path.with_suffix(".md")
                manifest.parent.mkdir(parents=True, exist_ok=True)
                listing = "\n".join(
                    f"- {p.relative_to(src)}" for p in sorted(src.rglob("*.py"))
                )
                manifest.write_text(
                    f"---\nname: {agent.name}\nkind: workflow\n---\n\n"
                    f"# {agent.name}\n\n{agent.description}\n\n"
                    f"## Source files\n\n{listing}\n",
                    encoding="utf-8",
                )
                copied += 1
                continue
            dest = agent.vendored_path
            if dest.exists() and not force:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        return copied, skipped


def _spec_from_dict(d: dict[str, Any]) -> AgentSpec:
    return AgentSpec(
        name=d["name"],
        ecosystem=Ecosystem(d["ecosystem"]),
        category=d["category"],
        description=d["description"],
        capabilities=list(d.get("capabilities", [])),
        source_repo=d["source_repo"],
        source_path=d.get("source_path", ""),
        model=d.get("model"),
        kind=d.get("kind", "agent"),
        server_command=d.get("server_command"),
    )


def _split_frontmatter(text: str) -> tuple[str, dict[str, Any]]:
    if not text.startswith("---"):
        return text, {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text, {}
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    body = parts[2].lstrip("\n")
    return body, fm


def _cli() -> None:
    """`python -m orchestrator.registry.loader --vendor` to copy source files in."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", action="store_true", help="Copy source files into registry/agents/")
    parser.add_argument("--force", action="store_true", help="Overwrite existing vendored files")
    parser.add_argument("--list", action="store_true", help="List all curated agents")
    args = parser.parse_args()

    reg = Registry.load()
    if args.list:
        for a in reg:
            print(f"  {a.ecosystem.value:18} {a.category:22} {a.name:32} — {a.description}")
        print(f"\nTotal: {len(reg)} agents across {len(reg.categories())} categories")
        return
    if args.vendor:
        copied, skipped = reg.vendor(force=args.force)
        print(f"Vendored {copied} agents into {VENDORED_DIR.relative_to(PROJECT_ROOT)} ({skipped} skipped)")
        return
    print(f"{len(reg)} agents loaded. Use --list or --vendor.")


if __name__ == "__main__":
    _cli()
