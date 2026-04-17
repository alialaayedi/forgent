"""User-scoped persistent config for forgent.

Lives at `~/.forgent/config.json` (override with `FORGENT_CONFIG`). Used for
preferences that must outlive a single MCP subprocess — the "ask once, never
again" flow for the optional status line lives here.

Kept intentionally tiny. If you're tempted to grow this into a config
framework, stop — project state belongs in MemoryStore, not here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    override = os.environ.get("FORGENT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".forgent" / "config.json"


StatuslineChoice = str  # "accepted" | "declined"


@dataclass
class ForgentConfig:
    """Loads and persists ~/.forgent/config.json.

    The file is written atomically (temp file + rename) so a crashed write
    never leaves a half-written JSON behind. If the file is missing or
    corrupted, we start fresh — no errors bubbled to the caller; this must
    never block an MCP tool call.
    """

    path: Path

    # -------------------------------------------------------------- load/save

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ForgentConfig":
        p = Path(path).expanduser() if path else _default_path()
        inst = cls(path=p)
        return inst

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # -------------------------------------------------------------- statusline

    def consent_prompted(self) -> bool:
        """Has the first-run banner been shown already?"""
        return bool(self._read().get("statusline_prompted"))

    def mark_consent_prompted(self) -> None:
        data = self._read()
        data["statusline_prompted"] = True
        self._write(data)

    def statusline_choice(self) -> StatuslineChoice | None:
        """What the user decided, or None if they haven't chosen."""
        val = self._read().get("statusline_choice")
        if val in ("accepted", "declined"):
            return val
        return None

    def record_statusline_choice(self, choice: StatuslineChoice) -> None:
        if choice not in ("accepted", "declined"):
            raise ValueError("choice must be 'accepted' or 'declined'")
        data = self._read()
        data["statusline_choice"] = choice
        data["statusline_prompted"] = True
        self._write(data)

    # -------------------------------------------------------------- statusline appearance

    def render_mode(self) -> str:
        """Which status-line render mode to use.

        One of 'auto', 'minimal', 'powerline', 'capsule', 'compact'.
        'auto' (default) picks powerline on Nerd-Font terminals, else minimal.
        """
        return str(self._read().get("render_mode") or "auto")

    def set_render_mode(self, mode: str) -> None:
        if mode not in ("auto", "minimal", "rich", "powerline", "capsule", "compact"):
            raise ValueError(f"unknown render mode {mode!r}")
        data = self._read()
        data["render_mode"] = mode
        self._write(data)

    def theme_name(self) -> str:
        return str(self._read().get("theme") or "dark")

    def set_theme(self, name: str) -> None:
        data = self._read()
        data["theme"] = name
        self._write(data)

    def segment_toggles(self) -> dict[str, bool]:
        """Per-segment on/off flags. Unset segments default to True."""
        val = self._read().get("segment_toggles") or {}
        return {k: bool(v) for k, v in val.items()} if isinstance(val, dict) else {}

    def set_segment(self, name: str, enabled: bool) -> None:
        data = self._read()
        toggles = data.get("segment_toggles") or {}
        if not isinstance(toggles, dict):
            toggles = {}
        toggles[name] = bool(enabled)
        data["segment_toggles"] = toggles
        self._write(data)

    # -------------------------------------------------------------- auto-compact

    def autocompact_pct(self) -> int | None:
        """The forgent-managed auto-compact threshold percent. None if unset."""
        val = self._read().get("autocompact_pct")
        if isinstance(val, int) and 1 <= val <= 99:
            return val
        return None

    def set_autocompact_pct(self, pct: int) -> None:
        if not isinstance(pct, int) or not (1 <= pct <= 99):
            raise ValueError("autocompact pct must be an int in 1..99")
        data = self._read()
        data["autocompact_pct"] = pct
        self._write(data)

    # -------------------------------------------------------------- team memory

    def team_id(self) -> str | None:
        val = self._read().get("team_id")
        return val if isinstance(val, str) and val else None

    def set_team_id(self, team_id: str | None) -> None:
        data = self._read()
        if team_id is None:
            data.pop("team_id", None)
        else:
            data["team_id"] = str(team_id)
        self._write(data)

    # -------------------------------------------------------------- budgets

    def default_budget_ms(self) -> int | None:
        val = self._read().get("default_budget_ms")
        return val if isinstance(val, int) and val > 0 else None

    def set_default_budget_ms(self, ms: int | None) -> None:
        data = self._read()
        if ms is None:
            data.pop("default_budget_ms", None)
        else:
            data["default_budget_ms"] = int(ms)
        self._write(data)

    # -------------------------------------------------------------- generic

    def get(self, key: str, default: Any = None) -> Any:
        return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def raw(self) -> dict[str, Any]:
        return self._read()
