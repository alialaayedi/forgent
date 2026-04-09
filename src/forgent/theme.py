"""Brand theming for the rich CLI.

Reads the canonical color file at assets/brand/colors.json so the CLI stays in
lockstep with the rest of the brand surfaces (banner, web, future site).

Usage:

    from forgent.theme import COLORS, RICH_THEME, console

    console.print("[accent]forgent[/accent]")
    console.print(Panel("hi", border_style=COLORS.accent))

If colors.json isn't on disk (e.g. running from a wheel that excluded assets/),
fall back to the hardcoded palette so nothing breaks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.theme import Theme


@dataclass(frozen=True)
class BrandColors:
    """Semantic brand tokens — use these in components, not raw hex strings."""
    bg: str
    bg_elevated: str
    fg: str
    fg_muted: str
    fg_secondary: str
    accent: str          # lobster pink — the spark
    accent_quiet: str    # rosy taupe
    border: str
    border_strong: str

    # Raw palette (for the rare case you need them)
    ink_black: str
    lobster_pink: str
    rosy_taupe: str
    silver: str
    alabaster_grey: str


# Hardcoded fallback — must match assets/brand/colors.json exactly.
_FALLBACK = BrandColors(
    bg="#071013",
    bg_elevated="#0e1a1f",
    fg="#dfe0e2",
    fg_muted="#aaaaaa",
    fg_secondary="#b7999c",
    accent="#eb5160",
    accent_quiet="#b7999c",
    border="#b7999c",
    border_strong="#eb5160",
    ink_black="#071013",
    lobster_pink="#eb5160",
    rosy_taupe="#b7999c",
    silver="#aaaaaa",
    alabaster_grey="#dfe0e2",
)


def _load_colors() -> BrandColors:
    # Try to find colors.json walking up from this file — works in editable
    # installs. In wheels we just use the fallback.
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "assets" / "brand" / "colors.json"
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                pal = data["palette"]
                sem = data["semantic"]
                return BrandColors(
                    bg=sem["bg"],
                    bg_elevated=sem["bg_elevated"],
                    fg=sem["fg"],
                    fg_muted=sem["fg_muted"],
                    fg_secondary=sem["fg_secondary"],
                    accent=sem["accent"],
                    accent_quiet=sem["accent_quiet"],
                    border=sem["border"],
                    border_strong=sem["border_strong"],
                    ink_black=pal["ink_black"]["hex"],
                    lobster_pink=pal["lobster_pink"]["hex"],
                    rosy_taupe=pal["rosy_taupe"]["hex"],
                    silver=pal["silver"]["hex"],
                    alabaster_grey=pal["alabaster_grey"]["hex"],
                )
            except (KeyError, json.JSONDecodeError):
                break
    return _FALLBACK


COLORS = _load_colors()


# Rich theme — these names are referenced as [accent], [muted], etc. in CLI
# output strings, so they stay readable even if the underlying hex changes.
RICH_THEME = Theme(
    {
        # semantic
        "accent":    f"bold {COLORS.accent}",
        "accent2":   COLORS.accent_quiet,
        "muted":     COLORS.fg_muted,
        "secondary": COLORS.fg_secondary,
        "fg":        COLORS.fg,

        # status (kept distinct from brand for legibility)
        "success":   "bold green",
        "error":     f"bold {COLORS.accent}",
        "warning":   "yellow",
        "info":      COLORS.fg_secondary,

        # panel/table conventions
        "title":     f"bold {COLORS.accent}",
        "subtitle":  COLORS.fg_secondary,
        "label":     COLORS.fg_muted,
        "value":     COLORS.fg,

        # rich's built-in semantic remaps so existing markup keeps working
        "cyan":      COLORS.accent,           # primary panel borders → brand pink
        "magenta":   COLORS.accent_quiet,     # secondary panel borders → taupe
        "yellow":    COLORS.fg_secondary,     # category chips → taupe
    }
)


# A pre-built console with the brand theme. Import this from cli.py instead
# of constructing your own Console.
console = Console(theme=RICH_THEME)
