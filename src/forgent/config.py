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

    # -------------------------------------------------------------- generic

    def get(self, key: str, default: Any = None) -> Any:
        return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def raw(self) -> dict[str, Any]:
        return self._read()
