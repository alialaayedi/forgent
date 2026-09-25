"""Claude Code hook handlers shipped with the forgent plugin.

The plugin's hooks/hooks.json calls `forgent hook <event>`; each handler
reads the hook JSON from stdin and prints the hook's JSON response. What
runs is controlled by one hook profile (ECC-style), read from
FORGENT_HOOK_PROFILE, then ~/.forgent/config.json, default "standard":

    off       nothing runs
    minimal   SessionStart: a short note about this project's forgent memory
    standard  minimal + Stop: one reminder to call report_outcome for a
              forgent session that was planned but never closed

Handlers must never break a Claude Code session: every failure path exits
quietly with no output.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROFILES = ("off", "minimal", "standard")
DEFAULT_PROFILE = "standard"

# SessionStart context is paid for on every session, so keep it small.
_CONTEXT_CAP_CHARS = 700
# Only sessions planned this recently count as "this conversation's" work.
_OPEN_SESSION_WINDOW_S = 3 * 60 * 60


def hook_profile() -> str:
    val = os.environ.get("FORGENT_HOOK_PROFILE")
    if not val:
        try:
            from forgent.config import ForgentConfig

            val = ForgentConfig.load().get("hook_profile")
        except Exception:
            val = None
    val = str(val or DEFAULT_PROFILE).strip().lower()
    return val if val in PROFILES else DEFAULT_PROFILE


def _read_input() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _db_path(payload: dict[str, Any]) -> Path:
    env = os.environ.get("FORGENT_DB")
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute() and payload.get("cwd"):
            p = Path(payload["cwd"]) / p
        return p
    return Path(payload.get("cwd") or ".") / "forgent.db"


def session_start_context(db: Path) -> str:
    """The additionalContext string for SessionStart. Pure; easy to test."""
    lines = [
        "forgent is available in this session: call its `advise_task` tool before "
        "non-trivial work to get a plan card, and `report_outcome` when done."
    ]
    if db.exists():
        from forgent.memory import MemoryStore

        mem = MemoryStore(db)
        stats = mem.stats()
        notes = len(mem.list_paths("/notes/"))
        outcomes = mem.recent_outcomes(limit=3)
        tasks = stats.get("task", 0)
        summary = f"Project memory: {tasks} planned task{'s' if tasks != 1 else ''}"
        if notes:
            summary += f", {notes} note topic{'s' if notes != 1 else ''} under /notes/"
        lines.append(summary + ".")
        if outcomes:
            lines.append("Recent outcomes: " + "; ".join(o.content[:120] for o in outcomes))
    text = " ".join(lines)
    return text if len(text) <= _CONTEXT_CAP_CHARS else text[: _CONTEXT_CAP_CHARS - 3] + "..."


def open_session_reminder(db: Path, now: float | None = None) -> str | None:
    """Reason text for a Stop block, or None. Marks the session so it fires once."""
    if not db.exists():
        return None
    from forgent.memory import MemoryStore

    mem = MemoryStore(db)
    since = (now or time.time()) - _OPEN_SESSION_WINDOW_S
    pending = mem.open_sessions(since)
    if not pending:
        return None
    sess = pending[0]
    mem.close_session(sess["id"], status="reminded")
    task = sess["task"][:100]
    return (
        f"forgent session {sess['id'][:8]} (\"{task}\") has no outcome yet. "
        f"Call forgent's `report_outcome` with session_id {sess['id']}, "
        "success true or false, and a one-line note, then finish. If this "
        "session's task is not done yet, report it as not successful with what remains."
    )


def run(event: str) -> int:
    """Entry point for `forgent hook <event>`. Always returns 0."""
    try:
        profile = hook_profile()
        if profile == "off":
            return 0
        payload = _read_input()
        db = _db_path(payload)
        if event == "session-start":
            ctx = session_start_context(db)
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}
            }))
        elif event == "stop" and profile == "standard":
            if payload.get("stop_hook_active"):
                return 0  # already continuing because of a Stop hook; never loop
            reason = open_session_reminder(db)
            if reason:
                print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        pass
    return 0
