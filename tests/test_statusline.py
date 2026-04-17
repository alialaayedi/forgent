"""Tests for the forgent status line + first-run consent flow."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from forgent.config import ForgentConfig
from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import Registry
from forgent import statusline as statusline_mod


# --------------------------------------------------------------------------- config


def test_config_starts_empty(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = ForgentConfig.load(cfg_path)
    assert cfg.consent_prompted() is False
    assert cfg.statusline_choice() is None


def test_config_records_prompted_flag(tmp_path):
    cfg = ForgentConfig.load(tmp_path / "config.json")
    cfg.mark_consent_prompted()
    # Reload from disk -- persistence check.
    cfg2 = ForgentConfig.load(tmp_path / "config.json")
    assert cfg2.consent_prompted() is True
    assert cfg2.statusline_choice() is None


def test_config_record_choice_also_marks_prompted(tmp_path):
    cfg = ForgentConfig.load(tmp_path / "config.json")
    cfg.record_statusline_choice("accepted")
    cfg2 = ForgentConfig.load(tmp_path / "config.json")
    assert cfg2.statusline_choice() == "accepted"
    assert cfg2.consent_prompted() is True


def test_config_rejects_invalid_choice(tmp_path):
    cfg = ForgentConfig.load(tmp_path / "config.json")
    with pytest.raises(ValueError):
        cfg.record_statusline_choice("maybe")


def test_config_handles_corrupted_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not json at all {{{", encoding="utf-8")
    cfg = ForgentConfig.load(path)
    # Corrupted state reads as empty; writes still work.
    assert cfg.consent_prompted() is False
    cfg.mark_consent_prompted()
    assert cfg.consent_prompted() is True


# --------------------------------------------------------------------------- render


def _plain_env(monkeypatch):
    monkeypatch.setenv("FORGENT_STATUSLINE_PLAIN", "1")


def test_render_line_always_returns_something(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "empty.db"))
    line = statusline_mod.render_line({"cwd": str(tmp_path)})
    assert "forgent" in line


def test_render_line_populated_includes_pack_and_wins(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    db = tmp_path / "forgent.db"
    mem = MemoryStore(db)
    sid = mem.start_session("task")
    mem.remember("plan", MemoryType.PLAN, session_id=sid, source="python-pro", tags=["plan"])
    for _ in range(3):
        mem.record_outcome(sid, True, agent_name="python-pro")
    mem.record_outcome(sid, False, agent_name="python-pro")
    mem.write_note("/notes/auth", "handler at src/api/auth.ts:42")

    monkeypatch.setenv("FORGENT_DB", str(db))
    line = statusline_mod.render_line(
        {"cwd": str(tmp_path), "model": {"id": "claude-opus-4-7", "display_name": "Opus 4.7"}}
    )
    assert "python-pro" in line
    assert "3W/1L" in line  # compact wins/losses format
    assert "1 note" in line  # singular form
    assert "Opus 4.7 (1M)" in line  # model with context window size


def test_render_line_never_raises(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    # Point at a directory that isn't a file -- should not crash.
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "nope"))
    line = statusline_mod.render_line({"cwd": "/nonexistent/path"})
    assert "forgent" in line


# --------------------------------------------------------------------------- context usage


def test_context_label_parses_transcript(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    tx = tmp_path / "transcript.jsonl"
    tx.write_text(
        "\n".join([
            json.dumps({"type": "user"}),
            json.dumps({
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 1000,
                        "cache_creation_input_tokens": 1000,
                        "cache_read_input_tokens": 138000,
                        "output_tokens": 200,
                    }
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "empty.db"))
    line = statusline_mod.render_line(
        {
            "cwd": str(tmp_path),
            "model": {"id": "claude-opus-4-7"},
            "transcript_path": str(tx),
        }
    )
    assert "ctx" in line
    assert "14%" in line or "15%" in line  # 140k/1M ~= 14%
    assert "til compact" in line


def test_context_label_flags_near_compact(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    tx = tmp_path / "transcript.jsonl"
    tx.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 920000,
                    "output_tokens": 0,
                }
            },
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "empty.db"))
    line = statusline_mod.render_line(
        {"cwd": str(tmp_path), "model": {"id": "claude-opus-4-7"}, "transcript_path": str(tx)}
    )
    # 92% >= default threshold -> should surface "compact now"
    assert "compact now" in line


def test_context_label_absent_when_no_transcript(monkeypatch, tmp_path):
    _plain_env(monkeypatch)
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "empty.db"))
    line = statusline_mod.render_line(
        {"cwd": str(tmp_path), "model": {"id": "claude-opus-4-7"}}
    )
    assert "ctx" not in line


# --------------------------------------------------------------------------- install/uninstall


def test_install_writes_settings_and_is_idempotent(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Also route Path.home() reliably.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    path1 = statusline_mod.install(scope="user")
    assert path1.exists()
    data = json.loads(path1.read_text(encoding="utf-8"))
    assert data["statusLine"]["command"] == "forgent-statusline"

    # Run again — idempotent.
    path2 = statusline_mod.install(scope="user")
    assert path2 == path1
    data2 = json.loads(path2.read_text(encoding="utf-8"))
    assert data2["statusLine"] == data["statusLine"]

    # Preserves other keys.
    data2["permissions"] = {"allow": ["Bash(git diff:*)"]}
    path2.write_text(json.dumps(data2), encoding="utf-8")
    statusline_mod.install(scope="user")
    merged = json.loads(path2.read_text(encoding="utf-8"))
    assert merged.get("permissions") == {"allow": ["Bash(git diff:*)"]}
    assert merged["statusLine"]["command"] == "forgent-statusline"


def test_uninstall_removes_statusline_only(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    statusline_mod.install(scope="user")

    # Add an unrelated key.
    settings_path = fake_home / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["permissions"] = {"allow": ["Bash(ls:*)"]}
    settings_path.write_text(json.dumps(data), encoding="utf-8")

    changed = statusline_mod.uninstall(scope="user")
    assert changed is True
    final = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "statusLine" not in final
    assert final.get("permissions") == {"allow": ["Bash(ls:*)"]}


def test_uninstall_no_op_when_not_ours(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    settings_path = fake_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"statusLine": {"type": "command", "command": "some-other-tool"}}),
        encoding="utf-8",
    )
    changed = statusline_mod.uninstall(scope="user")
    assert changed is False
    # Other tool left intact.
    final = json.loads(settings_path.read_text(encoding="utf-8"))
    assert final["statusLine"]["command"] == "some-other-tool"


# --------------------------------------------------------------------------- first-run banner


def test_first_advise_triggers_banner_then_suppressed(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGENT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "forgent.db"))
    # The mcp_server module holds lazy singletons; reset them so we get a
    # fresh config tied to tmp_path.
    import importlib

    import forgent.mcp_server as mcp_mod
    importlib.reload(mcp_mod)

    out1 = asyncio.run(mcp_mod.advise_task("test task one"))
    assert "forgent -- first-run setup" in out1
    assert "forgent statusline enable" in out1

    out2 = asyncio.run(mcp_mod.advise_task("test task two"))
    assert "forgent -- first-run setup" not in out2


def test_banner_suppressed_if_already_prompted(monkeypatch, tmp_path):
    # Pre-populate the config as already prompted.
    cfg = ForgentConfig.load(tmp_path / "config.json")
    cfg.mark_consent_prompted()

    monkeypatch.setenv("FORGENT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("FORGENT_DB", str(tmp_path / "forgent.db"))
    import importlib

    import forgent.mcp_server as mcp_mod
    importlib.reload(mcp_mod)

    out = asyncio.run(mcp_mod.advise_task("task"))
    assert "forgent -- first-run setup" not in out
