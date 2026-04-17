"""forgent status line for Claude Code.

Runs as a shell command Claude Code invokes every prompt. Reads the JSON
context Claude Code passes on stdin and emits one colored line on stdout.

Layout (missing pieces are dropped, not replaced with placeholders):

    forgent > <pack> . <W/L> . <notes>  |  <path> <branch><*>  |  <bar> <pct>% ctx . <til compact>  |  <model> (<ctx>)

Example:

    forgent > python-pro . 3W/1L . 2 notes  |  ~/Documents/tovo [b]main*  |  [====    ] 38% ctx . 54% til compact  |  Opus 4.7 (1M)

Design choices (inspired by community status-line designs):
  - Forgent signature and active pack are linked with ">" to read as a
    single phrase: "forgent is guiding via python-pro".
  - Groups are split by a thin vertical bar; fields within a group by a
    middle dot. This makes the line easy to scan at a glance.
  - Context usage gets an 8-cell visual bar in addition to %, so the
    "how full" signal lands before you read the number.
  - Model includes its context window size -- Opus 4.7 (1M) vs Haiku
    4.5 (200k). Tells you at a glance whether you have headroom.

The left two groups are forgent-unique (pack, outcome ratio, notes).
The middle two are standard engineer context (path/branch, ctx bar).
The right is model info. Missing pieces collapse so the line never
fills with placeholders.

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

def _dot() -> str:
    return f" {_c('·', _GRAY, dim=True)} "


def _bar_sep() -> str:
    return f"  {_c('│', _GRAY, dim=True)}  "


# Branch glyph. Unicode U+2387 (ALTERNATIVE KEY SYMBOL) is the de-facto git
# branch icon used by starship/powerline/p10k. Renders on every terminal
# font we care about.
_BRANCH_GLYPH = "\u2387"


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
    wins = _outcome_compact(cwd)
    notes = _notes_count(cwd)
    forged = _forged_count(cwd)

    # standard engineer context
    path_label = _path_label(cwd)
    branch, dirty = _git_branch(cwd)
    model_label = _model_with_context(ctx)
    context_block = _context_label(ctx)

    groups: list[str] = []

    # -- group 1: forgent signature + active pack + outcome ratio --
    forgent_chunk = _c("forgent", _MAGENTA, bold=True)
    if pack:
        arrow = _c("\u203a", _GRAY, dim=True)  # ›
        forgent_chunk = f"{forgent_chunk} {arrow} {_c(pack, _YELLOW, bold=True)}"
    pieces_a: list[str] = [forgent_chunk]
    if wins:
        pieces_a.append(wins)
    if notes:
        pieces_a.append(_c(f"{notes} {'note' if notes == 1 else 'notes'}", _GRAY))
    if forged:
        pieces_a.append(_c(f"{forged} forged", _GRAY))
    groups.append(_dot().join(pieces_a))

    # -- group 2: cwd + branch --
    path_part = _c(path_label, _CYAN, bold=True)
    if branch:
        glyph = _c(_BRANCH_GLYPH, _CYAN)
        branch_short = _truncate(branch, 20)
        branch_text = _c(branch_short, _CYAN)
        dirty_part = _c("*", _RED, bold=True) if dirty else ""
        groups.append(f"{path_part}  {glyph} {branch_text}{dirty_part}")
    else:
        groups.append(path_part)

    # -- group 3: context usage (bar + pct + compact countdown) --
    if context_block:
        groups.append(context_block)

    # -- group 4: model with context window size --
    if model_label:
        groups.append(_c(model_label, _GRAY))

    return _bar_sep().join(groups)


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


def _outcome_compact(cwd: Path) -> str:
    """Color-graded 'NW/ML' compact string. Empty when no outcomes yet.

    Compact form reads faster at a glance than '3/4 wins'. Green when the
    ratio is healthy, orange on the border, red when the agent is losing.
    """
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
    losses = total - wins
    ratio = wins / total if total else 0
    if ratio >= 0.75:
        color = _GREEN
    elif ratio >= 0.5:
        color = _ORANGE
    else:
        color = _RED
    return _c(f"{wins}W/{losses}L", color, bold=True)


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


def _path_label(cwd: Path, max_parts: int = 3) -> str:
    """Shortened-but-contextual path. Replaces $HOME with '~' and trims depth.

    Examples:
        /Users/foo                            -> ~
        /Users/foo/Documents/tovo             -> ~/Documents/tovo
        /Users/foo/code/work/deep/nested/x    -> ~/.../deep/nested/x
        /opt/shared/thing                     -> /opt/shared/thing (no home)
    """
    home = Path.home()
    try:
        if cwd == home:
            return "~"
        rel = cwd.relative_to(home)
        parts = rel.parts
        if len(parts) <= max_parts:
            return "~/" + "/".join(parts)
        return "~/.../" + "/".join(parts[-max_parts:])
    except ValueError:
        # Not under home -- fall back to the last few segments.
        parts = cwd.parts
        if len(parts) <= max_parts + 1:  # +1 for the root
            return str(cwd)
        return ".../" + "/".join(parts[-max_parts:])


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
    """Just the model display name, no context size."""
    model = ctx.get("model")
    if isinstance(model, dict):
        display = model.get("display_name") or model.get("id") or ""
        return str(display)
    if isinstance(model, str):
        return model
    return ""


def _model_with_context(ctx: dict[str, Any]) -> str:
    """'Opus 4.7 (1M)' / 'Haiku 4.5 (200k)'. Falls back to just the name."""
    name = _model_label(ctx)
    if not name:
        return ""
    max_tokens = _model_context_tokens(ctx)
    if not max_tokens:
        return name
    ctx_label = _humanize_context_window(max_tokens)
    return f"{name} ({ctx_label})" if ctx_label else name


def _humanize_context_window(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if v.is_integer() else f"{v:.1f}M"
    if n >= 1_000:
        return f"{int(round(n / 1000))}k"
    return str(n)


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
    """Render '[bar] NN% ctx . NN% til compact', color-graded.

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
        bold = True
    elif pct >= threshold - 15:
        color = _ORANGE
        bold = True
    else:
        color = _GRAY
        bold = False

    bar = _progress_bar(pct, color=color)
    pct_label = _c(f"{pct:.0f}% ctx", color, bold=bold)
    if pct >= threshold:
        compact_label = _c("compact now", _RED, bold=True)
    else:
        compact_label = _c(f"{til_compact}% til compact", _GRAY, dim=True)
    return f"{bar} {pct_label}{_dot()}{compact_label}"


def _progress_bar(pct: float, *, cells: int = 8, color: str = _GRAY) -> str:
    """Render a chunky ASCII/Unicode bar like '[====    ]'.

    Uses heavy/light box-drawing characters so the bar reads crisply in
    a monospace font. Colors just the filled portion; empty cells are
    dim gray.
    """
    filled = max(0, min(cells, round(pct / 100 * cells)))
    empty = cells - filled
    # U+25B0 BLACK RIGHT-POINTING TRIANGLE? No -- use U+2588/U+2591 for
    # classic block-shaded bars. Widely available; matches starship.
    filled_char = "\u2588"  # █
    empty_char = "\u2591"   # ░
    filled_part = _c(filled_char * filled, color, bold=True) if filled else ""
    empty_part = _c(empty_char * empty, _GRAY, dim=True) if empty else ""
    return f"{filled_part}{empty_part}"


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
