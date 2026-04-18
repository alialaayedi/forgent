"""forgent status line v2 for Claude Code.

Runs as a shell command Claude Code invokes every prompt. Reads the JSON
context Claude Code passes on stdin and emits one colored line (or two, on
narrow terminals) on stdout.

Design goals -- this is meant to be the best-in-class status line for a
Claude Code user. That means:

    Four render modes, user-selectable:
        minimal    -- foreground color only, plain ASCII (no-NF default)
        powerline  -- classic solid pills with U+E0B0 triangle separators
        capsule    -- rounded pills with U+E0B6 / U+E0B4 end caps
        compact    -- single line, aggressive abbreviations, narrow terminals

    Three themes (dark / light / highcontrast) via src/forgent/themes.py

    Priority-based flex collapse when the line overflows the terminal
    width. If even the smallest collapsed form doesn't fit, fall back to
    two lines split at a clean segment boundary.

    A rich segment catalog sourced from Claude Code's own status-line JSON:
        - forgent signature + active knowledge pack
        - cwd + git branch + dirty + ahead/behind counts
        - 8-cell visual context bar + percent + countdown to auto-compact
        - session cost, 5-hour rate-limit usage, token I/O
        - model display name + context window size
        - wall clock, session age

    Cost and rate-limit data comes from the official Claude Code status-line
    schema (`cost.*`, `rate_limits.five_hour.*`). No other status line on the
    market ties these together with per-project forgent memory signals.

Public entry points:

    render_line(ctx, *, mode=None, theme=None, width=None) -> str
        Pure. Never raises. Falls back gracefully when data is missing.

    main()
        `forgent-statusline` console entry point. Reads Claude Code's JSON
        on stdin, renders, writes to stdout. On any failure, emits a
        minimal "forgent" line so the status bar is never blank.

    install(scope="user", ...) / uninstall(scope="user")
        Patch ~/.claude/settings.json (or ./.claude/settings.json) with the
        statusLine command. Preserves all unrelated keys and also manages
        the CLAUDE_AUTOCOMPACT_PCT_OVERRIDE env var so forgent's compact
        threshold stays in sync with Claude Code's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from forgent.config import ForgentConfig
from forgent.memory import MemoryStore, MemoryType
from forgent import themes
from forgent.themes import Palette


# --------------------------------------------------------------------------- primitives

# Powerline / capsule separator glyphs. These live in the Nerd Font
# "powerline" block (U+E0B0..). On non-Nerd terminals we fall back to ASCII.
_GLYPH_ARROW_RIGHT = "\ue0b0"       # solid right arrow -- classic powerline
_GLYPH_ARROW_RIGHT_THIN = "\ue0b1"  # thin chevron (between pills of same bg)
_GLYPH_CAP_LEFT = "\ue0b6"          # rounded left cap (capsule mode)
_GLYPH_CAP_RIGHT = "\ue0b4"         # rounded right cap
_GLYPH_BRANCH = "\u2387"            # branch icon (non-NF, widely supported)
_GLYPH_CLOCK = "\u25f7"             # clock-ish (non-NF)

# Per-segment glyph icons. Pulled from the Geometric Shapes (U+25xx) and
# Dingbats (U+27xx) Unicode blocks -- monochrome text glyphs, no emoji
# rendering, no Nerd-Font dependency. They ride along with the text in rich
# mode to give the line visible anchors while staying in line-height and
# inheriting the segment's foreground color (unlike emoji, which are
# colored by the font and don't pick up the palette).
_ICONS: dict[str, str] = {
    "forgent": "\u2726",      # ✦ black four-pointed star -- forgent signature
    "agent": "\u25c6",        # ◆ black diamond -- active knowledge pack
    "wins": "\u2713",         # ✓ check -- outcomes
    "notes": "\u270e",        # ✎ lower-right pencil -- host-written notes
    "dir": "\u25b8",          # ▸ black right-pointing small triangle -- path
    "git": "",                # branch glyph is already in the segment text
    "ctx_bar": "",            # the bar itself is the icon
    "cost": "",               # "$" is already in the segment text
    "rate_5h": "\u25f7",      # ◷ circle with upper-right quadrant -- clockish
    "tokens_io": "",          # arrows already in text
    "model": "\u25ce",        # ◎ bullseye -- the active model
    "time": "\u25f4",         # ◴ circle with upper-left quadrant
    "session_age": "\u25d4",  # ◔ circle with upper-right quadrant black
}


@dataclass
class RenderContext:
    """Everything the renderers need to produce a line. Built once per call."""

    ctx: dict[str, Any]
    palette: Palette
    mode: str
    nerd_font: bool
    plain: bool
    width: int
    config: ForgentConfig
    toggles: dict[str, bool] = field(default_factory=dict)

    def segment_enabled(self, name: str) -> bool:
        """Config + default toggles. Unset = enabled. `time` default = off."""
        if name in self.toggles:
            return self.toggles[name]
        # Off by default: time, session_age (noise for most flows)
        if name in ("time", "session_age"):
            return False
        return True


@dataclass
class Segment:
    """One rendered piece. Assembled into a line by the mode-specific layout."""

    key: str           # segment id for toggling / priority
    text: str          # the visible text
    fg: int            # foreground 256-color index
    bg: int            # background 256-color index (used in pill modes)
    priority: int      # higher = kept longer during flex collapse
    bold: bool = False


# --------------------------------------------------------------------------- coloring


def _fg(idx: int, rc: RenderContext) -> str:
    return "" if rc.plain else themes.fg(idx)


def _bg(idx: int, rc: RenderContext) -> str:
    return "" if rc.plain else themes.bg(idx)


def _reset(rc: RenderContext) -> str:
    return "" if rc.plain else themes.reset()


def _bold(rc: RenderContext) -> str:
    return "" if rc.plain else themes.bold()


def _dim(rc: RenderContext) -> str:
    return "" if rc.plain else themes.dim()


def _fmt_minimal(seg: Segment, rc: RenderContext) -> str:
    """minimal mode: just bold foreground, no background."""
    prefix = _bold(rc) if seg.bold else ""
    return f"{prefix}{_fg(seg.fg, rc)}{seg.text}{_reset(rc)}"


def _fmt_pill(seg: Segment, rc: RenderContext, *, cap_left: str = "", cap_right: str = "") -> str:
    """pill mode: text on a solid background with optional caps."""
    lead = ""
    trail = ""
    if cap_left:
        lead = f"{_fg(seg.bg, rc)}{cap_left}{_reset(rc)}"
    bold = _bold(rc) if seg.bold else ""
    body = f"{_fg(seg.fg, rc)}{_bg(seg.bg, rc)}{bold} {seg.text} {_reset(rc)}"
    if cap_right:
        trail = f"{_fg(seg.bg, rc)}{cap_right}{_reset(rc)}"
    return f"{lead}{body}{trail}"


def _transition(prev_bg: int, next_bg: int, glyph: str, rc: RenderContext) -> str:
    """Powerline transition: prev_bg arrow on next_bg background."""
    if rc.plain:
        return glyph
    return f"{_fg(prev_bg, rc)}{_bg(next_bg, rc)}{glyph}{_reset(rc)}"


def _trailing(prev_bg: int, glyph: str, rc: RenderContext) -> str:
    """Final arrow -- prev_bg foreground on default background."""
    if rc.plain:
        return glyph
    return f"{_fg(prev_bg, rc)}{glyph}{_reset(rc)}"


# --------------------------------------------------------------------------- data pieces


def _project_db(cwd: Path) -> Path:
    """Walk up from cwd looking for a forgent.db; env override wins."""
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


def _active_pack(cwd: Path, ctx: dict[str, Any]) -> str:
    """Prefer Claude Code's live agent field; fall back to forgent's last PLAN."""
    agent = ctx.get("agent")
    if isinstance(agent, dict):
        name = agent.get("name")
        if isinstance(name, str) and name:
            return name
    store = _store_or_none(cwd)
    if store is None:
        return ""
    try:
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT source FROM memories "
            "WHERE type=? AND source IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (MemoryType.PLAN.value,),
        ).fetchall()
        return rows[0]["source"] if rows else ""
    except Exception:
        return ""


def _outcome_stats(cwd: Path) -> tuple[int, int] | None:
    """Return (wins, losses) or None if no outcomes yet."""
    store = _store_or_none(cwd)
    if store is None:
        return None
    try:
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT content FROM memories WHERE type=?",
            (MemoryType.OUTCOME.value,),
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    wins = sum(1 for r in rows if "outcome=success" in (r["content"] or ""))
    return (wins, len(rows) - wins)


def _notes_count(cwd: Path) -> int:
    store = _store_or_none(cwd)
    if store is None:
        return 0
    try:
        row = store._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM memories WHERE type=? AND tags LIKE '%host-note%'",
            (MemoryType.NOTE.value,),
        ).fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _forged_count(cwd: Path) -> int:
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
        parts = cwd.parts
        if len(parts) <= max_parts + 1:
            return str(cwd)
        return ".../" + "/".join(parts[-max_parts:])


def _git_info(cwd: Path) -> tuple[str, bool, int, int]:
    """(branch, dirty, ahead, behind). Empty branch = not a git repo."""
    try:
        b = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=1.5,
        )
        if b.returncode != 0:
            return ("", False, 0, 0)
        branch = b.stdout.strip()
        if not branch:
            return ("", False, 0, 0)
        s = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=cwd, capture_output=True, text=True, timeout=1.5,
        )
        dirty = bool(s.stdout.strip()) if s.returncode == 0 else False
        u = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=1.5,
        )
        behind = ahead = 0
        if u.returncode == 0 and u.stdout.strip():
            parts = u.stdout.strip().split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
        return (branch, dirty, ahead, behind)
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return ("", False, 0, 0)


# --- Context usage, from Claude Code's JSON + transcript fallback ---

# Max context in tokens for known models. The Claude Code status-line JSON
# now ships `context_window.context_window_size`, so prefer that; this
# dict is the fallback for older CC versions.
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-5": 200_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def _compact_threshold_pct() -> int:
    """Forgent's auto-compact target. Env > config > default 60."""
    raw = os.environ.get("FORGENT_COMPACT_PCT")
    if raw:
        try:
            v = int(raw)
            if 1 <= v <= 99:
                return v
        except ValueError:
            pass
    try:
        v = ForgentConfig.load().autocompact_pct()
        if v is not None:
            return v
    except Exception:
        pass
    return 60


def _context_pct(ctx: dict[str, Any]) -> float | None:
    """Percentage of context window in use, or None if unknown."""
    # Prefer the new Claude Code field.
    cw = ctx.get("context_window")
    if isinstance(cw, dict):
        pct = cw.get("used_percentage")
        if isinstance(pct, (int, float)):
            return float(pct)
    # Fall back to parsing transcript_path tail.
    used = _transcript_tokens(ctx)
    cap = _context_cap(ctx)
    if used > 0 and cap > 0:
        return min(100.0, (used / cap) * 100.0)
    return None


def _context_cap(ctx: dict[str, Any]) -> int:
    cw = ctx.get("context_window")
    if isinstance(cw, dict):
        size = cw.get("context_window_size")
        if isinstance(size, int) and size > 0:
            return size
    model = ctx.get("model") or {}
    mid = ""
    if isinstance(model, dict):
        mid = str(model.get("id") or "")
    elif isinstance(model, str):
        mid = model
    return _MODEL_CONTEXT_TOKENS.get(mid, 200_000)


def _transcript_tokens(ctx: dict[str, Any]) -> int:
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


def _transcript_tokens_io(ctx: dict[str, Any]) -> tuple[int, int]:
    """(input, output) from the latest assistant usage."""
    path_str = ctx.get("transcript_path")
    if not isinstance(path_str, str) or not path_str:
        return (0, 0)
    path = Path(path_str).expanduser()
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 128 * 1024)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return (0, 0)
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
        inp = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        out = int(usage.get("output_tokens") or 0)
        if inp or out:
            return (inp, out)
    return (0, 0)


def _extract_usage(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    u = entry.get("usage")
    if isinstance(u, dict):
        return u
    m = entry.get("message")
    if isinstance(m, dict):
        u = m.get("usage")
        if isinstance(u, dict):
            return u
    return None


def _progress_bar_str(pct: float, cells: int = 8) -> str:
    """Plain-text progress bar; caller colorizes."""
    filled = max(0, min(cells, round(pct / 100 * cells)))
    return "\u2588" * filled + "\u2591" * (cells - filled)


def _humanize_ctx(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if v.is_integer() else f"{v:.1f}M"
    if n >= 1_000:
        return f"{int(round(n / 1000))}k"
    return str(n)


def _humanize_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def _model_with_cap(ctx: dict[str, Any]) -> str:
    m = ctx.get("model")
    name = ""
    if isinstance(m, dict):
        name = str(m.get("display_name") or m.get("id") or "")
    elif isinstance(m, str):
        name = m
    if not name:
        return ""
    cap = _context_cap(ctx)
    return f"{name} ({_humanize_ctx(cap)})" if cap else name


def _session_age_str(ctx: dict[str, Any]) -> str:
    """'3m' / '12m' / '1h23m'. Empty if session_id not present."""
    sid = ctx.get("session_id")
    if not isinstance(sid, str) or not sid:
        return ""
    started = _SESSION_STARTS.get(sid)
    now = time.time()
    if started is None:
        _SESSION_STARTS[sid] = now
        return "0m"
    delta = int(now - started)
    if delta < 60:
        return f"{delta}s"
    mins = delta // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    return f"{hours}h{mins % 60:02d}m"


# Cross-call memo: first time we see a session id we stamp it, so "session
# age" reflects elapsed wall clock from our perspective. Persists only for
# the lifetime of the statusline subprocess.
_SESSION_STARTS: dict[str, float] = {}


# --------------------------------------------------------------------------- segments builder


def _build_segments(rc: RenderContext) -> list[Segment]:
    """Produce the full segment list in display order. No layout yet."""
    ctx = rc.ctx
    p = rc.palette
    cwd = Path(ctx.get("cwd") or os.getcwd())
    out: list[Segment] = []

    # 1. forgent signature
    if rc.segment_enabled("forgent"):
        out.append(Segment("forgent", "forgent", p.forgent[0], p.forgent[1], 100, bold=True))

    # 2. active agent / knowledge pack
    if rc.segment_enabled("agent"):
        pack = _active_pack(cwd, ctx)
        if pack:
            out.append(Segment("agent", pack, p.agent[0], p.agent[1], 95, bold=True))

    # 3. wins (compact NW/ML)
    if rc.segment_enabled("wins"):
        stats = _outcome_stats(cwd)
        if stats:
            wins, losses = stats
            total = wins + losses
            ratio = wins / total if total else 0
            if ratio >= 0.75:
                colors = p.wins_ok
            elif ratio >= 0.5:
                colors = p.wins_warn
            else:
                colors = p.wins_bad
            out.append(Segment("wins", f"{wins}W/{losses}L", colors[0], colors[1], 65, bold=True))

    # 4. notes
    if rc.segment_enabled("notes"):
        n = _notes_count(cwd)
        if n:
            label = "note" if n == 1 else "notes"
            out.append(Segment("notes", f"{n} {label}", p.neutral[0], 0, 40))

    # 5. cwd
    if rc.segment_enabled("dir"):
        out.append(Segment("dir", _path_label(cwd), p.dir[0], p.dir[1], 90, bold=True))

    # 6. git
    if rc.segment_enabled("git"):
        branch, dirty, ahead, behind = _git_info(cwd)
        if branch:
            parts: list[str] = []
            glyph = _GLYPH_BRANCH
            short = _truncate(branch, 20)
            parts.append(f"{glyph} {short}")
            if ahead or behind:
                arrows: list[str] = []
                if ahead:
                    arrows.append(f"\u2191{ahead}")  # ↑
                if behind:
                    arrows.append(f"\u2193{behind}")  # ↓
                parts.append(" ".join(arrows))
            if dirty:
                parts.append("*")
            colors = p.git_dirty if dirty else p.git
            out.append(Segment("git", " ".join(parts), colors[0], colors[1], 85, bold=dirty))

    # 7. context bar + pct
    if rc.segment_enabled("ctx"):
        pct = _context_pct(ctx)
        if pct is not None:
            threshold = _compact_threshold_pct()
            if pct >= threshold:
                colors = p.ctx_bad
            elif pct >= threshold - 15:
                colors = p.ctx_warn
            else:
                colors = p.ctx_ok
            bar = _progress_bar_str(pct)
            text = f"{bar} {pct:.0f}%"
            out.append(Segment("ctx_bar", text, colors[0], colors[1], 98, bold=(pct >= threshold - 15)))
            # Compact countdown as its own segment so it drops independently.
            if rc.segment_enabled("compact_warn"):
                if pct >= threshold:
                    out.append(Segment("compact_warn", "compact now", p.ctx_bad[0], p.ctx_bad[1], 92, bold=True))
                else:
                    til = max(0, int(round(threshold - pct)))
                    out.append(Segment("compact_warn", f"{til}% til compact", p.neutral[0], 0, 92))

    # 8. cost
    if rc.segment_enabled("cost"):
        cost = ctx.get("cost")
        if isinstance(cost, dict):
            usd = cost.get("total_cost_usd")
            if isinstance(usd, (int, float)) and usd > 0:
                out.append(Segment("cost", f"${usd:.2f}", p.cost[0], p.cost[1], 55, bold=True))

    # 9. rate limits (5h window)
    if rc.segment_enabled("rate_5h"):
        rl = ctx.get("rate_limits")
        if isinstance(rl, dict):
            five = rl.get("five_hour")
            if isinstance(five, dict):
                used_pct = five.get("used_percentage")
                if isinstance(used_pct, (int, float)) and used_pct > 0:
                    out.append(Segment("rate_5h", f"5h {used_pct:.0f}%", p.rate[0], p.rate[1], 60))

    # 10. tokens IO
    if rc.segment_enabled("tokens_io"):
        inp, outp = _transcript_tokens_io(ctx)
        if inp or outp:
            text = f"\u2193 {_humanize_tokens(inp)} \u2191 {_humanize_tokens(outp)}"
            out.append(Segment("tokens_io", text, p.tokens[0], p.tokens[1], 45))

    # 11. model + context cap -- high priority, this is critical info
    if rc.segment_enabled("model"):
        label = _model_with_cap(ctx)
        if label:
            out.append(Segment("model", label, p.model[0], p.model[1], 93))

    # 12. wall clock (opt-in)
    if rc.segment_enabled("time"):
        out.append(Segment("time", time.strftime("%H:%M"), p.time[0], p.time[1], 30))

    # 13. session age (opt-in)
    if rc.segment_enabled("session_age"):
        age = _session_age_str(rc.ctx)
        if age:
            out.append(Segment("session_age", age, p.time[0], p.time[1], 25))

    return out


# --------------------------------------------------------------------------- layouts


def _layout_minimal(segs: list[Segment], rc: RenderContext) -> str:
    """Plain-text-with-color layout, now with glyph icons for visual anchors.

    No backgrounds, no glyph separators -- works on any terminal, including
    dumb ones. Icons are Unicode geometric shapes / dingbats (not emoji),
    so they stay monochrome and inherit the segment's foreground color.
    """
    if not segs:
        return ""
    arrow = f" {_dim(rc)}{themes.fg(rc.palette.neutral[0]) if not rc.plain else ''}\u203a{_reset(rc)} "
    dot = f" {_dim(rc)}{themes.fg(rc.palette.neutral[0]) if not rc.plain else ''}\u00b7{_reset(rc)} "
    bar = f"  {_dim(rc)}{themes.fg(rc.palette.neutral[0]) if not rc.plain else ''}\u2502{_reset(rc)}  "

    grouped = _group_for_minimal(segs)
    rendered_groups: list[str] = []
    for group in grouped:
        chunks = [_fmt_minimal_with_icon(s, rc) for s in group]
        if len(group) >= 2 and group[0].key == "forgent" and group[1].key == "agent":
            head = f"{chunks[0]}{arrow}{chunks[1]}"
            tail = dot.join(chunks[2:])
            rendered_groups.append(head if not tail else f"{head}{dot}{tail}")
        else:
            rendered_groups.append(dot.join(chunks))
    return bar.join(rendered_groups)


def _fmt_minimal_with_icon(seg: Segment, rc: RenderContext) -> str:
    """Minimal formatter that prepends the segment's glyph icon if any."""
    icon = _ICONS.get(seg.key, "")
    text = f"{icon} {seg.text}" if icon else seg.text
    prefix = _bold(rc) if seg.bold else ""
    return f"{prefix}{_fg(seg.fg, rc)}{text}{_reset(rc)}"


def _layout_rich(segs: list[Segment], rc: RenderContext) -> str:
    """Bubble-shaped pills with glyph icons. No Nerd-Font glyphs required.

    Each segment is wrapped in half-circle caps (U+25D6 ◖ and U+25D7 ◗)
    colored as the segment's background on the default terminal background.
    This produces real round bubble shapes on any terminal -- no Nerd
    Font needed, works identically in iTerm / Alacritty / VS Code / Warp /
    Terminal.app / Ghostty / you name it.

    Pills are separated by a single space so each bubble floats
    independently.
    """
    if not segs:
        return ""
    cap_left = "\u25d6"   # ◖ LEFT HALF BLACK CIRCLE
    cap_right = "\u25d7"  # ◗ RIGHT HALF BLACK CIRCLE
    out: list[str] = []
    for s in segs:
        icon = _ICONS.get(s.key, "")
        label = f"{icon} {s.text}" if icon else s.text
        pill_seg = Segment(s.key, label, s.fg, s.bg, s.priority, bold=s.bold)
        if rc.plain:
            out.append(_fmt_pill(pill_seg, rc))
            continue
        # Left cap in the pill's bg color on default bg, then the pill body
        # with bg fill, then the right cap mirroring. Resets between so
        # the caps don't pick up the body's bg.
        left = f"{themes.fg(s.bg)}{cap_left}{_reset(rc)}"
        right = f"{themes.fg(s.bg)}{cap_right}{_reset(rc)}"
        bold = _bold(rc) if s.bold else ""
        body = f"{_fg(s.fg, rc)}{_bg(s.bg, rc)}{bold}{label}{_reset(rc)}"
        out.append(f"{left}{body}{right}")
    return " ".join(out)


def _group_for_minimal(segs: list[Segment]) -> list[list[Segment]]:
    """Group segments into major clusters for the minimal layout."""
    clusters: dict[str, list[Segment]] = {"forgent": [], "where": [], "ctx": [], "model": [], "aux": []}
    for s in segs:
        if s.key in ("forgent", "agent", "wins", "notes"):
            clusters["forgent"].append(s)
        elif s.key in ("dir", "git"):
            clusters["where"].append(s)
        elif s.key in ("ctx_bar", "compact_warn"):
            clusters["ctx"].append(s)
        elif s.key in ("model", "cost", "rate_5h", "tokens_io"):
            clusters["model"].append(s)
        else:
            clusters["aux"].append(s)
    return [v for v in clusters.values() if v]


def _layout_powerline(segs: list[Segment], rc: RenderContext) -> str:
    """Solid pill segments with U+E0B0 triangle transitions."""
    if not segs:
        return ""
    if rc.plain or not rc.nerd_font:
        return _layout_minimal(segs, rc)
    glyph = _GLYPH_ARROW_RIGHT
    out: list[str] = []
    for i, s in enumerate(segs):
        body = _fmt_pill(s, rc)
        out.append(body)
        if i + 1 < len(segs):
            next_bg = segs[i + 1].bg
            out.append(_transition(s.bg, next_bg, glyph, rc))
    out.append(_trailing(segs[-1].bg, glyph, rc))
    return "".join(out)


def _layout_capsule(segs: list[Segment], rc: RenderContext) -> str:
    """Rounded pills with U+E0B6 / U+E0B4 end caps; each pill is free-standing."""
    if not segs:
        return ""
    if rc.plain or not rc.nerd_font:
        return _layout_minimal(segs, rc)
    out: list[str] = []
    for s in segs:
        out.append(_fmt_pill(s, rc, cap_left=_GLYPH_CAP_LEFT, cap_right=_GLYPH_CAP_RIGHT))
    return " ".join(out)


def _layout_compact(segs: list[Segment], rc: RenderContext) -> str:
    """Ultra-tight single line for narrow terminals."""
    if not segs:
        return ""
    kept = [s for s in segs if s.priority >= 80 or s.key in ("wins", "ctx_bar")]
    return " ".join(_fmt_minimal(s, rc) for s in kept)


# --------------------------------------------------------------------------- wrapping


def _visible_len(s: str) -> int:
    """Length ignoring ANSI escape sequences."""
    import re
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def _render_for_width(segs: list[Segment], rc: RenderContext) -> str:
    """Render with priority-based flex collapse + optional wrap to 2 lines."""
    layout_fn = {
        "minimal": _layout_minimal,
        "rich": _layout_rich,
        "powerline": _layout_powerline,
        "capsule": _layout_capsule,
        "compact": _layout_compact,
    }.get(rc.mode, _layout_rich)

    current = list(segs)
    line = layout_fn(current, rc)
    if _visible_len(line) <= rc.width:
        return line

    # Drop lowest-priority segments until it fits, preserving display order.
    survivors = list(current)
    while survivors and _visible_len(layout_fn(survivors, rc)) > rc.width:
        # find the lowest-priority survivor and drop it
        lowest = min(range(len(survivors)), key=lambda i: survivors[i].priority)
        survivors.pop(lowest)
    if survivors:
        line = layout_fn(survivors, rc)
        if _visible_len(line) <= rc.width:
            return line

    # Last resort: split across two lines at the highest priority boundary.
    # Put priority >= 80 on line 1 (the "where am I / what's running" stuff),
    # the rest on line 2.
    line1 = [s for s in segs if s.priority >= 80]
    line2 = [s for s in segs if s.priority < 80]
    if line1 and line2:
        return layout_fn(line1, rc) + "\n" + layout_fn(line2, rc)
    return line  # give up and let it wrap naturally


# --------------------------------------------------------------------------- utilities


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)] + "\u2026"


def _resolve_mode(config: ForgentConfig) -> str:
    explicit = os.environ.get("FORGENT_STATUSLINE_MODE")
    if explicit in ("minimal", "rich", "powerline", "capsule", "compact"):
        return explicit
    configured = config.render_mode()
    if configured in ("minimal", "rich", "powerline", "capsule", "compact"):
        return configured
    # auto -- powerline when the terminal ships Nerd Fonts, else rich (bg
    # colored pills with glyph icons -- works anywhere). minimal is only
    # picked when the user explicitly opts in via config.
    return "powerline" if themes.supports_nerd_font() else "rich"


def _resolve_theme(config: ForgentConfig) -> Palette:
    explicit = os.environ.get("FORGENT_STATUSLINE_THEME")
    if explicit:
        return themes.theme(explicit)
    return themes.theme(config.theme_name())


# --------------------------------------------------------------------------- public render


def render_line(
    ctx: dict[str, Any] | None = None,
    *,
    mode: str | None = None,
    theme_name: str | None = None,
    width: int | None = None,
) -> str:
    """Build the status line. Pure + exception-safe.

    Args:
        ctx: the Claude Code status-line JSON context (or {}).
        mode: explicit render mode override. Otherwise resolved from env
            > config > NF-detection.
        theme_name: explicit theme override. Otherwise resolved from env
            > config > dark.
        width: explicit width override (columns). Otherwise os-detected.

    Returns:
        A single string -- may contain a \\n when we wrapped to two lines.
    """
    ctx = ctx or {}
    try:
        config = ForgentConfig.load()
    except Exception:
        # If config can't load for any reason, use a stub so we still render.
        class _Stub:
            def render_mode(self) -> str: return "auto"
            def theme_name(self) -> str: return "dark"
            def segment_toggles(self) -> dict[str, bool]: return {}
            def autocompact_pct(self): return None
        config = _Stub()  # type: ignore[assignment]

    try:
        rc = RenderContext(
            ctx=ctx,
            palette=themes.theme(theme_name) if theme_name else _resolve_theme(config),
            mode=mode or _resolve_mode(config),
            nerd_font=themes.supports_nerd_font(),
            plain=themes.colors_disabled(),
            width=width or themes.terminal_width(),
            config=config,  # type: ignore[arg-type]
            toggles=config.segment_toggles(),
        )
        segs = _build_segments(rc)
        if not segs:
            return "forgent"
        return _render_for_width(segs, rc)
    except Exception:
        # Last-resort fallback -- never crash the status bar.
        return "forgent"


# --------------------------------------------------------------------------- install / uninstall


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


def install(
    scope: str = "user",
    command: str = "forgent-statusline",
    *,
    autocompact_pct: int | None = 60,
) -> Path:
    """Wire forgent-statusline into Claude Code's settings.json.

    Idempotent. Preserves unrelated keys. When `autocompact_pct` is given,
    also sets the `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` env var so Claude Code
    compacts at that threshold. Pass None to leave auto-compact untouched.
    """
    path = _settings_path(scope)
    data = _read_settings(path)
    data["statusLine"] = {
        "type": "command",
        "command": command,
        "padding": 0,
    }
    if autocompact_pct is not None:
        if not (1 <= autocompact_pct <= 99):
            raise ValueError("autocompact_pct must be in 1..99")
        env_block = data.get("env")
        if not isinstance(env_block, dict):
            env_block = {}
        env_block["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(autocompact_pct)
        data["env"] = env_block
        try:
            ForgentConfig.load().set_autocompact_pct(int(autocompact_pct))
        except Exception:
            pass
    _write_settings(path, data)
    return path


def set_autocompact(pct: int | None, scope: str = "user") -> Path:
    """Only manage the auto-compact env var; don't touch statusLine.

    `pct=None` removes the override entirely.
    """
    path = _settings_path(scope)
    data = _read_settings(path)
    env_block = data.get("env")
    if not isinstance(env_block, dict):
        env_block = {}
    if pct is None:
        env_block.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
        if env_block:
            data["env"] = env_block
        else:
            data.pop("env", None)
    else:
        if not (1 <= pct <= 99):
            raise ValueError("pct must be in 1..99")
        env_block["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(pct)
        data["env"] = env_block
        try:
            ForgentConfig.load().set_autocompact_pct(int(pct))
        except Exception:
            pass
    _write_settings(path, data)
    return path


def uninstall(scope: str = "user") -> bool:
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
    """`forgent-statusline` entry point. Never crashes the status bar."""
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
        line = "forgent"
    sys.stdout.write(line)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
