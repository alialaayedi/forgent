"""forgent status line for Claude Code.

Runs as a shell command Claude Code invokes every prompt. Reads the JSON
context Claude Code passes on stdin and emits one colored line on stdout.

Layout (missing pieces are dropped, not replaced with placeholders):

    forgent . <pack> . <wins> . <notes> . <forged>  |  <dir>@<branch><*> . <model> . <sid>

    - pack      last routed knowledge pack (from forgent's project memory)
    - wins      outcome ratio across all agents, color-graded
    - notes     count of host-written breadcrumbs (/notes/*)
    - forged    count of auto/manually forged specialists (marker of depth)
    - dir       basename of cwd (or "~" if home)
    - branch    current git branch
    - *         present when the working tree is dirty
    - model     the model id Claude Code is currently using
    - sid       short session id from Claude Code (the *host* session,
                not the forgent session — useful for correlating logs)

The forgent-specific half (pack/wins/notes/forged) is what makes this
status line unique. The right-hand half is standard engineer context.

Two public entry points:

    render_line(ctx) -> str
        Pure function. Given the Claude Code hook context dict (may be
        empty), return a single line. Never raises.

    main()
        stdio entry point wired as `forgent-statusline` in pyproject.
        Reads stdin, calls render_line, writes to stdout. Any failure
        falls back to a minimal "forgent" line so Claude Code never sees
        an empty status.

Install/uninstall helpers patch Claude Code's settings.json for the
user or project scope. They're idempotent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from forgent.memory import MemoryStore, MemoryType


# --------------------------------------------------------------------------- colors

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

# 256-color palette picks that render well on both light and dark terminals.
_MAGENTA = "\033[38;5;170m"  # forgent signature
_YELLOW = "\033[38;5;178m"   # active knowledge pack
_CYAN = "\033[38;5;39m"      # project@branch
_GREEN = "\033[38;5;35m"     # healthy outcome ratio
_ORANGE = "\033[38;5;208m"   # warning outcome ratio
_RED = "\033[38;5;167m"      # failing outcome ratio + dirty marker
_GRAY = "\033[38;5;245m"     # neutral counts / model


def _c(text: str, color: str, *, bold: bool = False, dim: bool = False) -> str:
    """Wrap text in ANSI codes. Respects NO_COLOR and non-TTY stdout."""
    if _colors_disabled():
        return text
    prefix = color
    if bold:
        prefix = _BOLD + prefix
    if dim:
        prefix = _DIM + prefix
    return f"{prefix}{text}{_RESET}"


def _colors_disabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return True
    if os.environ.get("FORGENT_STATUSLINE_PLAIN"):
        return True
    return False


# --------------------------------------------------------------------------- render

def _sep() -> str:
    return f" {_c('.', _GRAY, dim=True)} "


def _divider() -> str:
    return f"  {_c('|', _GRAY, dim=True)}  "


def render_line(ctx: dict[str, Any] | None = None) -> str:
    """Build the status line. Pure + exception-safe."""
    ctx = ctx or {}
    try:
        return _render(ctx)
    except Exception:
        return _c("forgent", _MAGENTA, bold=True)


def _render(ctx: dict[str, Any]) -> str:
    cwd = Path(ctx.get("cwd") or os.getcwd())

    # forgent-local pieces
    pack = _active_pack(cwd)
    wins = _outcome_summary(cwd)
    notes = _notes_count(cwd)
    forged = _forged_count(cwd)

    # standard engineer context
    dir_label = _dir_label(cwd)
    branch, dirty = _git_branch(cwd)
    model = _model_label(ctx)
    sid = _short_session(ctx)
    context_block = _context_label(ctx)

    # -- left half: forgent-specific --
    left: list[str] = [_c("forgent", _MAGENTA, bold=True)]
    if pack:
        left.append(_c(pack, _YELLOW, bold=True))
    if wins:
        left.append(wins)
    if notes:
        label = "note" if notes == 1 else "notes"
        left.append(_c(f"{notes} {label}", _GRAY))
    if forged:
        left.append(_c(f"{forged} forged", _GRAY))

    # -- right half: repo / model context --
    right: list[str] = []
    if branch:
        project = _c(dir_label, _CYAN, bold=True)
        branch_short = _truncate(branch, 22)
        branch_part = _c(f"@{branch_short}", _CYAN)
        dirty_part = _c("*", _RED, bold=True) if dirty else ""
        right.append(f"{project}{branch_part}{dirty_part}")
    else:
        right.append(_c(dir_label, _CYAN))
    if model:
        right.append(_c(model, _GRAY))
    if context_block:
        right.append(context_block)
    if sid:
        right.append(_c(sid, _GRAY, dim=True))

    sep = _sep()
    left_str = sep.join(left)
    right_str = sep.join(right)
    return f"{left_str}{_divider()}{right_str}"


# --------------------------------------------------------------------------- pieces

def _project_db(cwd: Path) -> Path:
    """Where the project-scoped forgent.db would live.

    Resolution rule: FORGENT_DB env overrides; otherwise walk up looking
    for an existing forgent.db so the status line tracks the nearest
    project, not the shell's cwd specifically.
    """
    override = os.environ.get("FORGENT_DB")
    if override:
        return Path(override).expanduser()
    here = cwd.resolve()
    for p in (here, *here.parents):
        candidate = p / "forgent.db"
        if candidate.exists():
            return candidate
    return here / "forgent.db"


def _store_or_none(cwd: Path) -> MemoryStore | None:
    db = _project_db(cwd)
    if not db.exists():
        return None
    try:
        return MemoryStore(db)
    except Exception:
        return None


def _active_pack(cwd: Path) -> str:
    """Last routed knowledge pack for this project, or '' if none yet."""
    store = _store_or_none(cwd)
    if store is None:
        return ""
    rows = store._conn.execute(  # noqa: SLF001 - internal read ok for status
        "SELECT source FROM memories "
        "WHERE type=? AND source IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 1",
        (MemoryType.PLAN.value,),
    ).fetchall()
    return rows[0]["source"] if rows else ""


def _outcome_summary(cwd: Path) -> str:
    """Color-graded 'N/M wins' string. Empty when no outcomes yet."""
    store = _store_or_none(cwd)
    if store is None:
        return ""
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT content FROM memories WHERE type=? ORDER BY created_at DESC",
        (MemoryType.OUTCOME.value,),
    ).fetchall()
    if not rows:
        return ""
    total = len(rows)
    wins = sum(1 for r in rows if "outcome=success" in (r["content"] or ""))
    ratio = wins / total if total else 0
    if ratio >= 0.75:
        color = _GREEN
    elif ratio >= 0.5:
        color = _ORANGE
    else:
        color = _RED
    return _c(f"{wins}/{total} wins", color, bold=True)


def _notes_count(cwd: Path) -> int:
    store = _store_or_none(cwd)
    if store is None:
        return 0
    row = store._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS n FROM memories WHERE type=? AND tags LIKE '%host-note%'",
        (MemoryType.NOTE.value,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _forged_count(cwd: Path) -> int:
    """Count forged specialists visible from this project's dynamic.yaml."""
    from forgent.registry.loader import PKG_DIR
    dynamic = PKG_DIR / "dynamic.yaml"
    if not dynamic.exists():
        return 0
    try:
        import yaml
        data = yaml.safe_load(dynamic.read_text(encoding="utf-8")) or {}
        agents = data.get("agents") or []
        return sum(1 for a in agents if isinstance(a, dict) and a.get("forged"))
    except Exception:
        return 0


def _dir_label(cwd: Path) -> str:
    home = Path.home()
    try:
        if cwd == home:
            return "~"
        rel = cwd.relative_to(home)
        # Show just the basename for depth >= 1 (keeps line tight).
        return rel.parts[-1] if rel.parts else cwd.name
    except ValueError:
        return cwd.name or str(cwd)


def _git_branch(cwd: Path) -> tuple[str, bool]:
    """Return (branch, is_dirty). Empty branch means 'not a git repo'."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if branch.returncode != 0:
            return ("", False)
        name = branch.stdout.strip()
        if not name:
            return ("", False)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
        return (name, dirty)
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return ("", False)


def _model_label(ctx: dict[str, Any]) -> str:
    model = ctx.get("model")
    if isinstance(model, dict):
        display = model.get("display_name") or model.get("id") or ""
        return str(display)
    if isinstance(model, str):
        return model
    return ""


def _short_session(ctx: dict[str, Any]) -> str:
    sid = ctx.get("session_id")
    if isinstance(sid, str) and sid:
        return sid[:8]
    return ""


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)] + "..."


# --------------------------------------------------------------------------- context

# Context windows for Claude Code-supported models. Kept small + explicit so
# we don't depend on network lookups from a status line (which must return
# in milliseconds). Unknown models fall back to 200k so we degrade gracefully.
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}

# Claude Code's default auto-compact threshold. Override via env for projects
# that raise or lower it. Must match your Claude Code settings.
_DEFAULT_COMPACT_PCT = 92


def _context_label(ctx: dict[str, Any]) -> str:
    """Render 'ctx 142k/1M . 14% . 78% til compact', color-graded.

    Returns '' when no usage data is available (no transcript yet, or on
    the very first prompt of a session). Never raises.
    """
    max_tokens = _model_context_tokens(ctx)
    used = _transcript_context_tokens(ctx)
    if used <= 0 or max_tokens <= 0:
        return ""

    pct = min(100.0, (used / max_tokens) * 100.0)
    threshold = _compact_threshold_pct()
    til_compact = max(0, int(round(threshold - pct)))

    # Color-grade by fullness.
    if pct >= threshold:
        color = _RED
    elif pct >= threshold - 15:
        color = _ORANGE
    else:
        color = _GRAY

    pct_label = f"{pct:.0f}%"
    compact_label = (
        "compact now" if pct >= threshold
        else f"{til_compact}% til compact"
    )
    return _c(
        f"ctx {pct_label} . {compact_label}",
        color,
        bold=(pct >= threshold - 15),
    )


def _model_context_tokens(ctx: dict[str, Any]) -> int:
    model = ctx.get("model") or {}
    model_id = ""
    if isinstance(model, dict):
        model_id = str(model.get("id") or "")
    elif isinstance(model, str):
        model_id = model
    return _MODEL_CONTEXT_TOKENS.get(model_id, 200_000)


def _compact_threshold_pct() -> int:
    raw = os.environ.get("FORGENT_COMPACT_PCT")
    if raw:
        try:
            val = int(raw)
            if 1 <= val <= 99:
                return val
        except ValueError:
            pass
    return _DEFAULT_COMPACT_PCT


def _transcript_context_tokens(ctx: dict[str, Any]) -> int:
    """Read the latest assistant usage from the Claude Code transcript.

    Claude Code writes one JSON per line to `transcript_path`. Assistant
    messages carry usage; we want the *most recent* one. To stay fast we
    read only the tail of the file (last ~128 KB).
    """
    path_str = ctx.get("transcript_path")
    if not isinstance(path_str, str) or not path_str:
        return 0
    path = Path(path_str).expanduser()
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 128 * 1024)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0

    # Walk lines backwards; return on the first assistant usage block.
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = _extract_usage(entry)
        if usage is None:
            continue
        total = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        if total > 0:
            return total
    return 0


def _extract_usage(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the usage dict out of a transcript entry. Shapes vary -- be lenient."""
    if not isinstance(entry, dict):
        return None
    # Top-level usage (some harness versions)
    usage = entry.get("usage")
    if isinstance(usage, dict):
        return usage
    # Nested under message (common Claude Code shape)
    message = entry.get("message")
    if isinstance(message, dict):
        usage = message.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def _humanize_tokens(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}M" if v < 10 else f"{int(round(v))}M"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.0f}k"
    return str(n)


# --------------------------------------------------------------------------- install

def _settings_path(scope: str) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        return Path.cwd() / ".claude" / "settings.json"
    raise ValueError(f"scope must be 'user' or 'project', got {scope!r}")


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def install(scope: str = "user", command: str = "forgent-statusline") -> Path:
    """Wire forgent-statusline into Claude Code's settings.json for the scope.

    Idempotent: running twice leaves the same config. Preserves any existing
    top-level keys (hooks, permissions, etc.).
    """
    path = _settings_path(scope)
    data = _read_settings(path)
    data["statusLine"] = {
        "type": "command",
        "command": command,
        "padding": 0,
    }
    _write_settings(path, data)
    return path


def uninstall(scope: str = "user") -> bool:
    """Remove the forgent statusLine entry. Returns True if anything changed."""
    path = _settings_path(scope)
    if not path.exists():
        return False
    data = _read_settings(path)
    sl = data.get("statusLine")
    if isinstance(sl, dict) and "forgent-statusline" in (sl.get("command") or ""):
        data.pop("statusLine", None)
        _write_settings(path, data)
        return True
    return False


def is_installed(scope: str = "user") -> bool:
    path = _settings_path(scope)
    data = _read_settings(path)
    sl = data.get("statusLine")
    return isinstance(sl, dict) and "forgent-statusline" in (sl.get("command") or "")


# --------------------------------------------------------------------------- entry

def main() -> None:
    """forgent-statusline CLI entry point. Never crashes the status bar."""
    ctx: dict[str, Any] = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                ctx = json.loads(raw)
                if not isinstance(ctx, dict):
                    ctx = {}
    except (json.JSONDecodeError, OSError):
        ctx = {}
    try:
        line = render_line(ctx)
    except Exception:
        line = _c("forgent", _MAGENTA, bold=True)
    sys.stdout.write(line)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
