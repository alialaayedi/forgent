"""Tests for the ECC-style install lifecycle and plugin hooks.

Nothing here touches the real Claude Code config: detection results are
constructed by hand and shell commands go through a recording fake runner.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from forgent import hooks, installer
from forgent.memory import MemoryStore

REPO = Path(__file__).resolve().parents[1]


def _det(**kw) -> installer.Detected:
    base = dict(
        claude_cli="/usr/bin/claude", forgent_mcp="/bin/forgent-mcp", uvx=None,
        api_key_set=True, plugin_installs=[], marketplace_known=False,
        mcp_scope=None, mcp_command=None, mcp_embeds_api_key=False,
    )
    base.update(kw)
    return installer.Detected(**base)


class FakeRunner:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, argv):
        self.calls.append(argv)
        rc = 1 if self.fail_on and self.fail_on in argv else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="boom" if rc else "")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGENT_CONFIG", str(tmp_path / "home" / "config.json"))
    monkeypatch.delenv("FORGENT_HOOK_PROFILE", raising=False)


def _argvs(plan):
    return [a.argv[1:] for a in plan.actions if a.argv]


def test_plugin_install_plan_fresh_machine():
    plan = installer.plan_install(_det(), channel="plugin", scope="user",
                                  hook_profile="standard", statusline=False)
    assert plan.blockers == []
    assert _argvs(plan) == [
        ["plugin", "marketplace", "add", "alialaayedi/forgent"],
        ["plugin", "install", "forgent@forgent", "--scope", "user"],
    ]


def test_plugin_plan_warns_on_stacked_mcp_and_replace_removes_it():
    det = _det(mcp_scope="user", mcp_command="/bin/forgent-mcp")
    plan = installer.plan_install(det, channel="plugin", scope="user",
                                  hook_profile="minimal", statusline=False)
    assert any("duplicates every tool" in w for w in plan.warnings)
    assert ["mcp", "remove", "forgent", "--scope", "user"] not in _argvs(plan)

    plan = installer.plan_install(det, channel="plugin", scope="user",
                                  hook_profile="minimal", statusline=False, replace_existing=True)
    assert _argvs(plan)[0] == ["mcp", "remove", "forgent", "--scope", "user"]


def test_mcp_channel_blocked_when_plugin_present_without_replace():
    det = _det(plugin_installs=[{"id": "forgent@forgent", "scope": "user", "enabled": True}])
    plan = installer.plan_install(det, channel="mcp", scope="user",
                                  hook_profile="off", statusline=False)
    assert plan.blockers and not plan.actions


def test_mcp_registration_never_embeds_secrets():
    plan = installer.plan_install(_det(), channel="mcp", scope="project",
                                  hook_profile="off", statusline=False)
    argv = _argvs(plan)[0]
    assert argv[:4] == ["mcp", "add", "--scope", "project"]
    assert not any("ANTHROPIC_API_KEY" in part for part in argv)


def test_missing_claude_cli_is_a_blocker():
    plan = installer.plan_install(_det(claude_cli=None), channel="plugin", scope="user",
                                  hook_profile="standard", statusline=False)
    assert plan.blockers


def test_execute_records_state_and_uninstall_reverses_only_recorded(tmp_path):
    state = installer.InstallState.load(tmp_path / "state.json")
    plan = installer.plan_install(_det(), channel="plugin", scope="local",
                                  hook_profile="minimal", statusline=False)
    results = installer.execute(plan, state, runner=FakeRunner())
    assert all(r.ok for r in results)
    reloaded = installer.InstallState.load(tmp_path / "state.json")
    assert {"marketplace", "plugin:local", "hooks"} <= set(reloaded.entries)
    assert hooks.hook_profile() == "minimal"

    det = _det(marketplace_known=True, mcp_scope="user",
               plugin_installs=[{"id": "forgent@forgent", "scope": "local", "enabled": True}])
    un = installer.plan_uninstall(reloaded, det)
    removes = _argvs(un)
    assert ["plugin", "uninstall", "forgent@forgent", "--scope", "local"] in removes
    assert not any(a[:2] == ["mcp", "remove"] for a in removes)  # not ours
    assert any("did not create" in w for w in un.warnings)
    installer.execute_uninstall(un, reloaded, runner=FakeRunner())
    assert installer.InstallState.load(tmp_path / "state.json").entries == {}


def test_execute_stops_at_first_failure(tmp_path):
    state = installer.InstallState.load(tmp_path / "state.json")
    plan = installer.plan_install(_det(), channel="plugin", scope="user",
                                  hook_profile="standard", statusline=False)
    results = installer.execute(plan, state, runner=FakeRunner(fail_on="marketplace"))
    assert len(results) == 1 and not results[0].ok
    assert state.entries == {}


def test_repair_reapplies_drifted_plugin(tmp_path):
    state = installer.InstallState.load(tmp_path / "state.json")
    state.record("plugin:user", kind="plugin", scope="user")
    plan = installer.plan_repair(_det(marketplace_known=True), state)
    assert _argvs(plan) == [["plugin", "install", "forgent@forgent", "--scope", "user"]]


def test_parse_mcp_get_scope_and_secret_flag():
    text = (
        "forgent:\n  Scope: Local config (private to you in this project)\n"
        "  Command: /x/forgent-mcp\n  Environment:\n    ANTHROPIC_API_KEY=sk-ant-xyz\n"
    )
    scope, command, embeds = installer._parse_mcp_get(text)
    assert (scope, command, embeds) == ("local", "/x/forgent-mcp", True)


def test_doctor_flags_stacking_and_secrets(tmp_path):
    det = _det(mcp_scope="user", mcp_embeds_api_key=True,
               plugin_installs=[{"id": "forgent@forgent", "scope": "user", "enabled": True}])
    checks = {c.name: c for c in installer.doctor(det, installer.InstallState.load(tmp_path / "s.json"))}
    assert checks["Stacking"].status == "warn"
    assert checks["Secrets"].status == "warn"
    assert "sk-ant" not in checks["Secrets"].detail


# --------------------------------------------------------------------------- hooks


def test_session_start_context_is_small_and_mentions_memory(tmp_path):
    db = tmp_path / "forgent.db"
    assert "advise_task" in hooks.session_start_context(db)
    mem = MemoryStore(db)
    mem.start_session("add refund endpoint")
    mem.write_note("/notes/stripe", "webhook handler at api/stripe.py")
    ctx = hooks.session_start_context(db)
    assert "1 planned task," in ctx and "1 note topic" in ctx
    assert len(ctx) <= 700


def test_stop_reminder_fires_once_per_open_session(tmp_path):
    db = tmp_path / "forgent.db"
    mem = MemoryStore(db)
    done = mem.start_session("finished task")
    mem.record_outcome(done, True)
    open_sid = mem.start_session("unfinished task")
    reason = hooks.open_session_reminder(db)
    assert reason and open_sid in reason and "report_outcome" in reason
    assert hooks.open_session_reminder(db) is None
    assert hooks.open_session_reminder(db, now=time.time() + 10 * 3600) is None


def test_hook_run_respects_profile_and_loop_guard(tmp_path, monkeypatch, capsys):
    db = tmp_path / "forgent.db"
    MemoryStore(db).start_session("open task")
    monkeypatch.setattr(hooks, "_read_input", lambda: {"cwd": str(tmp_path), "stop_hook_active": True})
    hooks.run("stop")
    assert capsys.readouterr().out == ""  # loop guard
    monkeypatch.setenv("FORGENT_HOOK_PROFILE", "minimal")
    monkeypatch.setattr(hooks, "_read_input", lambda: {"cwd": str(tmp_path)})
    hooks.run("stop")
    assert capsys.readouterr().out == ""  # Stop hook is standard-only
    hooks.run("session-start")
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


# --------------------------------------------------------------------------- plugin files


def test_plugin_manifests_are_consistent():
    from forgent import __version__

    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((REPO / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert market["name"] == installer.MARKETPLACE_NAME
    entry = market["plugins"][0]
    assert entry["name"] == plugin["name"] == installer.PLUGIN_NAME
    assert entry["source"] == "./plugin"
    assert plugin["version"] == entry["version"] == __version__
    mcp = json.loads((REPO / "plugin" / ".mcp.json").read_text())
    assert mcp["mcpServers"]["forgent"]["args"] == ["forgent-mcp"]
    for skill in ("plan", "outcome", "configure"):
        assert (REPO / "plugin" / "skills" / skill / "SKILL.md").exists()
