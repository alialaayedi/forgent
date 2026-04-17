"""Status-line themes + terminal-capability detection.

Palettes follow Starship's pastel-powerline recipe, adapted for forgent's
brand (magenta signature). Each theme ships matching fg/bg pairs for every
pill segment, plus a `neutral` color for empty areas.

Nerd Font / terminal detection is best-effort and conservative: if we're
not sure the terminal renders powerline glyphs, we default to `minimal`
mode so users on plain xterm still get a usable line.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """Color pairs for a status-line theme.

    Each entry is a (fg, bg) pair as 256-color indices. `fg` is what the
    segment text uses; `bg` is used in powerline/capsule modes for the pill
    background. In minimal mode we ignore bg and just color the foreground.
    """

    name: str
    forgent: tuple[int, int]       # signature chip
    agent: tuple[int, int]         # active knowledge pack
    wins_ok: tuple[int, int]       # outcomes, healthy ratio
    wins_warn: tuple[int, int]     # outcomes, borderline ratio
    wins_bad: tuple[int, int]      # outcomes, failing ratio
    dir: tuple[int, int]           # cwd
    git: tuple[int, int]           # branch + counts
    git_dirty: tuple[int, int]     # dirty marker
    ctx_ok: tuple[int, int]        # context bar, plenty of room
    ctx_warn: tuple[int, int]      # context bar, nearing compact
    ctx_bad: tuple[int, int]       # context bar, at/past compact
    cost: tuple[int, int]
    rate: tuple[int, int]
    tokens: tuple[int, int]
    model: tuple[int, int]
    time: tuple[int, int]
    neutral: tuple[int, int]       # separators, muted text


# Default "dark" palette — Starship pastel-powerline-inspired, forgent-branded.
# Background indices are picked so adjacent pills contrast at the glyph edge.
DARK = Palette(
    name="dark",
    forgent=(231, 125),      # white on deep magenta
    agent=(232, 178),        # near-black on amber
    wins_ok=(232, 35),       # near-black on green
    wins_warn=(232, 208),    # near-black on orange
    wins_bad=(231, 167),     # white on red
    dir=(231, 39),           # white on sky blue
    git=(232, 45),           # near-black on cyan
    git_dirty=(231, 167),    # white on red
    ctx_ok=(231, 35),        # white on green
    ctx_warn=(232, 208),     # near-black on orange
    ctx_bad=(231, 167),      # white on red
    cost=(232, 179),         # near-black on gold
    rate=(232, 141),         # near-black on purple
    tokens=(231, 61),        # white on muted blue
    model=(231, 67),         # white on dusty blue
    time=(231, 240),         # white on charcoal
    neutral=(245, 0),        # gray on default
)

# Light palette — muted versions tuned for light backgrounds.
LIGHT = Palette(
    name="light",
    forgent=(231, 89),       # white on dark magenta
    agent=(232, 220),
    wins_ok=(232, 71),
    wins_warn=(232, 214),
    wins_bad=(231, 124),
    dir=(231, 31),
    git=(232, 37),
    git_dirty=(231, 124),
    ctx_ok=(231, 71),
    ctx_warn=(232, 214),
    ctx_bad=(231, 124),
    cost=(232, 179),
    rate=(232, 97),
    tokens=(231, 24),
    model=(231, 61),
    time=(231, 244),
    neutral=(244, 0),
)

# High-contrast palette — ANSI 16-color only, no mid-tones. For accessibility
# and dumb terminals.
HIGHCONTRAST = Palette(
    name="highcontrast",
    forgent=(15, 5),         # bright white on magenta
    agent=(0, 11),           # black on bright yellow
    wins_ok=(0, 10),         # black on bright green
    wins_warn=(0, 3),        # black on yellow
    wins_bad=(15, 9),        # bright white on bright red
    dir=(15, 4),             # bright white on blue
    git=(15, 6),             # bright white on cyan
    git_dirty=(15, 9),
    ctx_ok=(15, 2),          # white on green
    ctx_warn=(0, 3),
    ctx_bad=(15, 1),         # white on red
    cost=(0, 11),
    rate=(15, 5),
    tokens=(15, 4),
    model=(15, 8),           # white on gray
    time=(15, 8),
    neutral=(7, 0),
)

_THEMES: dict[str, Palette] = {
    "dark": DARK,
    "light": LIGHT,
    "highcontrast": HIGHCONTRAST,
}


def theme(name: str | None = None) -> Palette:
    """Resolve a theme name to a palette. Falls back to dark."""
    if not name:
        return DARK
    return _THEMES.get(name.lower(), DARK)


def available_themes() -> list[str]:
    return list(_THEMES.keys())


# --------------------------------------------------------------------------- capabilities

# Terminals that we *know* ship with decent Nerd-Font support by default or
# that users who set them up as their primary shell almost always have
# Nerd Fonts configured. Conservative list -- add only after verification.
_NF_TERMINALS: frozenset[str] = frozenset({
    "iTerm.app",
    "WezTerm",
    "Alacritty",
    "kitty",
    "Ghostty",
    "WarpTerminal",
    "Hyper",
    "Apple_Terminal",  # users with Homebrew usually have Nerd Fonts
})


def supports_nerd_font() -> bool:
    """Best-effort: does the current terminal render powerline/NF glyphs?

    Checks in order:
      1. FORGENT_STATUSLINE_CHARSET=text -> False (user opt-out)
      2. FORGENT_STATUSLINE_NERD_FONT set -> use that (1/true -> True,
         0/false -> False)
      3. TERM_PROGRAM in known-good list -> True
      4. Fallback: False (conservative -- minimal mode is the safe default)
    """
    if os.environ.get("FORGENT_STATUSLINE_CHARSET", "").lower() == "text":
        return False
    explicit = os.environ.get("FORGENT_STATUSLINE_NERD_FONT")
    if explicit is not None:
        return explicit.lower() in ("1", "true", "yes", "on")
    term_program = os.environ.get("TERM_PROGRAM") or ""
    if term_program in _NF_TERMINALS:
        return True
    # Also accept any terminal that sets TERM to include "kitty" / "alacritty"
    term = os.environ.get("TERM") or ""
    if "kitty" in term or "alacritty" in term:
        return True
    return False


def supports_truecolor() -> bool:
    """Does the terminal support 24-bit color? Used for smooth gradients."""
    colorterm = os.environ.get("COLORTERM", "").lower()
    return colorterm in ("truecolor", "24bit")


def terminal_width(default: int = 120) -> int:
    """Terminal width for wrap decisions. Honors COLUMNS, then os query."""
    cols = os.environ.get("COLUMNS")
    if cols and cols.isdigit():
        return int(cols)
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


# --------------------------------------------------------------------------- low-level ANSI

# All ANSI we emit. Centralized so the powerline/capsule/minimal renderers
# share one primitive.

_RESET = "\x1b[0m"


def fg(idx: int) -> str:
    """Foreground color escape for a 256-color palette index."""
    return f"\x1b[38;5;{idx}m"


def bg(idx: int) -> str:
    """Background color escape."""
    return f"\x1b[48;5;{idx}m"


def bold() -> str:
    return "\x1b[1m"


def dim() -> str:
    return "\x1b[2m"


def reset() -> str:
    return _RESET


def colors_disabled() -> bool:
    """Respect NO_COLOR and the forgent-specific plain-mode env."""
    if os.environ.get("NO_COLOR"):
        return True
    if os.environ.get("FORGENT_STATUSLINE_PLAIN"):
        return True
    return False
