"""Guided install, doctor, repair, and uninstall for forgent in Claude Code.

Modeled on ECC's install lifecycle: one recommended path, an explicit
preflight before any write, and an install-state manifest so later
commands only touch what forgent itself installed.

Two install channels -- pick one per scope, never both:

    plugin  Claude Code plugin `forgent@forgent`: MCP server + skills
            (/forgent:plan, /forgent:outcome, /forgent:configure) + hooks.
    mcp     Bare MCP registration via `claude mcp add`. Lowest context cost:
            no hooks, no skills.

Install state lives at ~/.forgent/install-state.json (next to config.json).
Every entry has a stable id, so re-running setup is idempotent and
uninstall never removes something forgent did not put there.

Secrets are never written anywhere by this module. The MCP server reads
ANTHROPIC_API_KEY from the environment Claude Code inherits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from forgent import __version__

MARKETPLACE_SOURCE = "alialaayedi/forgent"
MARKETPLACE_NAME = "forgent"
PLUGIN_NAME = "forgent"
PLUGIN_ID = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
MCP_NAME = "forgent"

CHANNELS = ("plugin", "mcp")
SCOPES = ("user", "project", "local")
HOOK_PROFILES = ("off", "minimal", "standard")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def forgent_home() -> Path:
    override = os.environ.get("FORGENT_CONFIG")
    if override:
        return Path(override).expanduser().parent
    return Path.home() / ".forgent"


# --------------------------------------------------------------------------- state


@dataclass
class InstallState:
    """The manifest of everything forgent installed, keyed by stable id."""

    path: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "InstallState":
        p = path or forgent_home() / "install-state.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries = data.get("entries", {}) if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            entries = {}
        return cls(path=p, entries=entries if isinstance(entries, dict) else {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"version": __version__, "entries": self.entries}
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def record(self, entry_id: str, **detail: Any) -> None:
        self.entries[entry_id] = {**detail, "installed_at": time.time(), "version": __version__}

    def forget(self, entry_id: str) -> None:
        self.entries.pop(entry_id, None)


# --------------------------------------------------------------------------- detection


@dataclass
class Detected:
    """What is already on this machine, gathered read-only."""

    claude_cli: str | None
    forgent_mcp: str | None
    uvx: str | None
    api_key_set: bool
    plugin_installs: list[dict[str, Any]]      # [{scope, enabled, version}]
    marketplace_known: bool
    mcp_scope: str | None                      # scope of a bare MCP registration
    mcp_command: str | None
    mcp_embeds_api_key: bool


def detect(runner: Runner = _default_runner) -> Detected:
    claude = shutil.which("claude")
    plugins: list[dict[str, Any]] = []
    marketplace_known = False
    mcp_scope = mcp_command = None
    embeds_key = False
    if claude:
        res = _safe_run(runner, [claude, "plugin", "list", "--json"])
        if res is not None and res.returncode == 0:
            try:
                for p in json.loads(res.stdout or "[]"):
                    if str(p.get("id", "")).startswith(f"{PLUGIN_NAME}@"):
                        plugins.append({
                            "id": p.get("id"),
                            "scope": p.get("scope"),
                            "enabled": bool(p.get("enabled", True)),
                            "version": p.get("version"),
                            "project": p.get("projectPath"),
                        })
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
        res = _safe_run(runner, [claude, "plugin", "marketplace", "list"])
        if res is not None and res.returncode == 0:
            marketplace_known = MARKETPLACE_NAME in (res.stdout or "")
        res = _safe_run(runner, [claude, "mcp", "get", MCP_NAME])
        if res is not None and res.returncode == 0:
            mcp_scope, mcp_command, embeds_key = _parse_mcp_get(res.stdout or "")
    return Detected(
        claude_cli=claude,
        forgent_mcp=shutil.which("forgent-mcp"),
        uvx=shutil.which("uvx"),
        api_key_set=bool(os.environ.get("ANTHROPIC_API_KEY")),
        plugin_installs=plugins,
        marketplace_known=marketplace_known,
        mcp_scope=mcp_scope,
        mcp_command=mcp_command,
        mcp_embeds_api_key=embeds_key,
    )


def _parse_mcp_get(text: str) -> tuple[str | None, str | None, bool]:
    """Pull scope/command out of `claude mcp get` output. Never keeps env values."""
    scope = command = None
    embeds_key = False
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("scope:"):
            # e.g. "User config (...)", "Local config (private to you in this project)"
            words = low.split(":", 1)[1].split()
            scope = words[0] if words and words[0] in SCOPES else None
        elif low.startswith("command:"):
            command = line.split(":", 1)[1].strip()
        elif line.startswith("ANTHROPIC_API_KEY="):
            embeds_key = len(line) > len("ANTHROPIC_API_KEY=")
    return scope, command, embeds_key


def _safe_run(runner: Runner, argv: list[str]) -> "subprocess.CompletedProcess[str] | None":
    try:
        return runner(argv)
    except (OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------------- planning


@dataclass
class Action:
    """One step in an install/repair/uninstall plan."""

    entry_id: str
    summary: str
    argv: list[str] | None = None          # shell command, if any
    apply: Callable[[], None] | None = None  # in-process change, if any
    record: dict[str, Any] | None = None   # state to record on success
    forget: bool = False                   # drop entry_id from state on success


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def plan_install(
    det: Detected,
    *,
    channel: str,
    scope: str,
    hook_profile: str,
    statusline: bool,
    replace_existing: bool = False,
    source: str = MARKETPLACE_SOURCE,
) -> Plan:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if hook_profile not in HOOK_PROFILES:
        raise ValueError(f"hook profile must be one of {HOOK_PROFILES}")

    plan = Plan()
    claude = det.claude_cli
    if not claude:
        plan.blockers.append(
            "Claude Code CLI (`claude`) is not on PATH. Install Claude Code first."
        )
        return plan

    if not det.api_key_set:
        plan.warnings.append(
            "ANTHROPIC_API_KEY is not set in this shell. forgent will run in "
            "heuristic mode until Claude Code inherits a key (export it in your "
            "shell profile; forgent never stores it)."
        )

    # ---- stacking checks: one channel per harness ----
    other_plugin = [p for p in det.plugin_installs if p["scope"] != scope]
    if channel == "plugin" and det.mcp_scope:
        msg = (
            f"forgent is also registered as a bare MCP server ({det.mcp_scope} scope). "
            "Plugin + bare MCP duplicates every tool."
        )
        if replace_existing:
            plan.actions.append(_remove_mcp_action(claude, det.mcp_scope))
        else:
            plan.warnings.append(msg + " Re-run with --replace to remove the bare registration.")
    if channel == "mcp" and det.plugin_installs:
        msg = "The forgent plugin is already installed; a bare MCP registration would duplicate it."
        if replace_existing:
            for p in det.plugin_installs:
                plan.actions.append(_remove_plugin_action(claude, p["scope"] or "user"))
        else:
            plan.blockers.append(msg + " Re-run with --replace to switch channels.")
            return plan
    if channel == "plugin" and other_plugin and not replace_existing:
        scopes = ", ".join(sorted({str(p["scope"]) for p in other_plugin}))
        plan.warnings.append(
            f"forgent plugin already installed at scope(s): {scopes}. Installing at "
            f"{scope} too is allowed but usually unintended."
        )

    # ---- the install itself ----
    if channel == "plugin":
        if not det.marketplace_known:
            plan.actions.append(Action(
                entry_id="marketplace",
                summary=f"Add marketplace `{MARKETPLACE_NAME}` from {source}",
                argv=[claude, "plugin", "marketplace", "add", source],
                record={"kind": "marketplace", "source": source},
            ))
        already = any(p["scope"] == scope for p in det.plugin_installs)
        if not already:
            plan.actions.append(Action(
                entry_id=f"plugin:{scope}",
                summary=f"Install plugin {PLUGIN_ID} ({scope} scope)",
                argv=[claude, "plugin", "install", PLUGIN_ID, "--scope", scope],
                record={"kind": "plugin", "scope": scope},
            ))
    else:
        needs_register = det.mcp_scope is None
        if det.mcp_scope and replace_existing:
            plan.actions.append(_remove_mcp_action(claude, det.mcp_scope))
            needs_register = True
        elif det.mcp_scope and det.mcp_scope != scope:
            plan.blockers.append(
                f"forgent is already registered at {det.mcp_scope} scope. Re-run with "
                f"--replace to move it to {scope} scope."
            )
            return plan
        if needs_register:
            command = det.forgent_mcp or "forgent-mcp"
            plan.actions.append(Action(
                entry_id=f"mcp:{scope}",
                summary=f"Register MCP server `{MCP_NAME}` ({scope} scope) -> {command}",
                argv=[claude, "mcp", "add", "--scope", scope, MCP_NAME, "--", command],
                record={"kind": "mcp", "scope": scope, "command": command},
            ))

    if det.mcp_embeds_api_key and not any(a.entry_id.startswith("mcp-remove") for a in plan.actions):
        plan.warnings.append(
            "The existing forgent MCP registration stores ANTHROPIC_API_KEY in "
            "~/.claude.json. Re-run with --replace to re-register without it, "
            "and consider rotating the key."
        )

    # ---- preferences ----
    plan.actions.append(Action(
        entry_id="hooks",
        summary=f"Set hook profile: {hook_profile}"
        + ("" if channel == "plugin" else " (hooks only run with the plugin channel)"),
        apply=lambda: _set_config("hook_profile", hook_profile),
        record={"kind": "config", "key": "hook_profile", "value": hook_profile},
    ))
    if statusline:
        sl_scope = "user" if scope == "user" else "project"
        plan.actions.append(Action(
            entry_id=f"statusline:{sl_scope}",
            summary=f"Install the forgent status line ({sl_scope} settings, auto-compact 60%)",
            apply=lambda: _install_statusline(sl_scope),
            record={"kind": "statusline", "scope": sl_scope},
        ))
    else:
        plan.actions.append(Action(
            entry_id="statusline-choice",
            summary="Skip the status line (and stop the first-run auto-install)",
            apply=_decline_statusline,
        ))
    return plan


def _remove_mcp_action(claude: str, scope: str) -> Action:
    return Action(
        entry_id=f"mcp-remove:{scope}",
        summary=f"Remove bare MCP registration `{MCP_NAME}` ({scope} scope)",
        argv=[claude, "mcp", "remove", MCP_NAME, "--scope", scope],
        forget=True,
    )


def _remove_plugin_action(claude: str, scope: str) -> Action:
    return Action(
        entry_id=f"plugin-remove:{scope}",
        summary=f"Uninstall plugin {PLUGIN_ID} ({scope} scope)",
        argv=[claude, "plugin", "uninstall", PLUGIN_ID, "--scope", scope],
        forget=True,
    )


def _set_config(key: str, value: Any) -> None:
    from forgent.config import ForgentConfig

    ForgentConfig.load().set(key, value)


def _install_statusline(scope: str) -> None:
    from forgent import statusline
    from forgent.config import ForgentConfig

    statusline.install(scope=scope, autocompact_pct=60)
    ForgentConfig.load().record_statusline_choice("accepted")


def _decline_statusline() -> None:
    from forgent.config import ForgentConfig

    ForgentConfig.load().record_statusline_choice("declined")


# --------------------------------------------------------------------------- execution


@dataclass
class StepResult:
    action: Action
    ok: bool
    detail: str = ""


def execute(plan: Plan, state: InstallState, runner: Runner = _default_runner) -> list[StepResult]:
    """Run each action in order; stop at the first failure. Records state as it goes."""
    results: list[StepResult] = []
    for action in plan.actions:
        ok, detail = True, ""
        try:
            if action.argv:
                res = runner(action.argv)
                ok = res.returncode == 0
                detail = _last_line(res)
            if ok and action.apply:
                action.apply()
        except Exception as exc:  # noqa: BLE001 -- surface any failure to the user
            ok, detail = False, str(exc)
        results.append(StepResult(action, ok, detail))
        if not ok:
            break
        if action.record is not None:
            state.record(action.entry_id, **action.record)
        if action.forget:
            state.forget(action.entry_id.replace("-remove", ""))  # mcp-remove:user -> mcp:user
        state.save()
    return results


def _last_line(res: "subprocess.CompletedProcess[str]") -> str:
    lines = (res.stderr or res.stdout or "").strip().splitlines()
    return lines[-1] if lines else ""


def plan_uninstall(state: InstallState, det: Detected) -> Plan:
    """Reverse every recorded entry. Unrecorded items are reported, not touched."""
    plan = Plan()
    claude = det.claude_cli
    for entry_id, entry in sorted(state.entries.items()):
        kind = entry.get("kind")
        scope = entry.get("scope", "user")
        if kind == "plugin" and claude:
            a = _remove_plugin_action(claude, scope)
            a.entry_id, a.forget = entry_id, False
            plan.actions.append(a)
        elif kind == "mcp" and claude:
            a = _remove_mcp_action(claude, scope)
            a.entry_id, a.forget = entry_id, False
            plan.actions.append(a)
        elif kind == "marketplace" and claude:
            plan.actions.append(Action(
                entry_id=entry_id,
                summary=f"Remove marketplace `{MARKETPLACE_NAME}`",
                argv=[claude, "plugin", "marketplace", "remove", MARKETPLACE_NAME],
            ))
        elif kind == "statusline":
            plan.actions.append(Action(
                entry_id=entry_id,
                summary=f"Remove the forgent status line ({scope} settings)",
                apply=lambda s=scope: _uninstall_statusline(s),
            ))
        elif kind == "config":
            plan.actions.append(Action(
                entry_id=entry_id,
                summary=f"Clear forgent preference `{entry.get('key')}`",
                apply=lambda k=entry.get("key"): _set_config(k, None),
            ))
    if det.mcp_scope and not any(e.get("kind") == "mcp" for e in state.entries.values()):
        plan.warnings.append(
            f"A forgent MCP registration exists ({det.mcp_scope} scope) that this "
            f"installer did not create; leaving it. Remove it with: "
            f"claude mcp remove {MCP_NAME} --scope {det.mcp_scope}"
        )
    plan.warnings.append(
        "Project memory databases (forgent.db) are kept. Delete them by hand if you want a clean slate."
    )
    return plan


def execute_uninstall(plan: Plan, state: InstallState, runner: Runner = _default_runner) -> list[StepResult]:
    results: list[StepResult] = []
    for action in plan.actions:
        ok, detail = True, ""
        try:
            if action.argv:
                res = runner(action.argv)
                ok = res.returncode == 0
                detail = _last_line(res)
            if ok and action.apply:
                action.apply()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, str(exc)
        results.append(StepResult(action, ok, detail))
        if ok:
            state.forget(action.entry_id)
            state.save()
    return results


def _uninstall_statusline(scope: str) -> None:
    from forgent import statusline

    statusline.uninstall(scope=scope)
    statusline.set_autocompact(None, scope=scope)


# --------------------------------------------------------------------------- doctor / repair


@dataclass
class Check:
    name: str
    status: str            # "ok" | "warn" | "fail"
    detail: str
    fix: str = ""


def doctor(det: Detected, state: InstallState) -> list[Check]:
    from forgent.llm import FORGE, PLANNER, ROUTER

    checks: list[Check] = []
    checks.append(Check("forgent", "ok", f"v{__version__} on Python {sys.version.split()[0]}"))

    try:
        try:
            import mcp.server.mcpserver  # noqa: F401
            mcp_detail = "mcp SDK 2.x"
        except ImportError:
            import mcp.server.fastmcp  # noqa: F401
            mcp_detail = "mcp SDK 1.x"
        checks.append(Check("MCP SDK", "ok", mcp_detail))
    except ImportError as exc:
        checks.append(Check("MCP SDK", "fail", str(exc), "pip install -U forgent"))

    if det.claude_cli:
        checks.append(Check("Claude Code CLI", "ok", det.claude_cli))
    else:
        checks.append(Check("Claude Code CLI", "fail", "`claude` not on PATH", "Install Claude Code"))

    if det.forgent_mcp:
        checks.append(Check("forgent-mcp", "ok", det.forgent_mcp))
    elif det.uvx:
        checks.append(Check("forgent-mcp", "ok", "not on PATH; the plugin launcher will use uvx"))
    else:
        checks.append(Check("forgent-mcp", "warn", "not on PATH and uvx missing",
                            "pipx install forgent  (or install uv)"))

    if det.api_key_set:
        checks.append(Check("API key", "ok", "ANTHROPIC_API_KEY is set (LLM planning enabled)"))
    else:
        checks.append(Check("API key", "warn", "ANTHROPIC_API_KEY not set; plans will be heuristic",
                            "export ANTHROPIC_API_KEY=... in your shell profile"))
    checks.append(Check(
        "Models", "ok",
        f"router {ROUTER.model()} ({ROUTER.effort()}), planner {PLANNER.model()} "
        f"({PLANNER.effort()}), forge {FORGE.model()} ({FORGE.effort()})",
    ))

    channels = []
    if det.plugin_installs:
        desc = ", ".join(
            f"{p['scope']}{'' if p['enabled'] else ' (disabled)'}" for p in det.plugin_installs
        )
        channels.append("plugin")
        disabled = [p for p in det.plugin_installs if not p["enabled"]]
        checks.append(Check("Plugin", "warn" if disabled else "ok", f"{PLUGIN_ID}: {desc}",
                            f"claude plugin enable {PLUGIN_ID}" if disabled else ""))
    if det.mcp_scope:
        channels.append("mcp")
        checks.append(Check("Bare MCP", "ok", f"{det.mcp_scope} scope -> {det.mcp_command}"))
    if not channels:
        checks.append(Check("Install", "fail", "forgent is not installed in Claude Code", "forgent setup"))
    elif len(channels) > 1:
        checks.append(Check("Stacking", "warn", "plugin AND bare MCP are both installed (duplicate tools)",
                            "forgent setup --channel plugin --replace"))

    if det.mcp_embeds_api_key:
        checks.append(Check("Secrets", "warn",
                            "ANTHROPIC_API_KEY is stored in the MCP registration (~/.claude.json)",
                            "forgent setup --channel mcp --replace, then rotate the key"))

    drift = drifted_entries(det, state)
    if drift:
        checks.append(Check("Install state", "warn",
                            f"recorded but missing: {', '.join(drift)}", "forgent repair"))
    elif state.entries:
        checks.append(Check("Install state", "ok", f"{len(state.entries)} managed entries"))

    from forgent.hooks import hook_profile

    checks.append(Check("Hook profile", "ok", hook_profile()))

    db = Path(os.environ.get("FORGENT_DB", "./forgent.db")).expanduser()
    parent = db.parent if str(db.parent) else Path(".")
    if os.access(parent, os.W_OK):
        checks.append(Check("Memory DB", "ok", f"{db} ({'exists' if db.exists() else 'created on first use'})"))
    else:
        checks.append(Check("Memory DB", "fail", f"{parent} is not writable", "set FORGENT_DB"))
    return checks


def drifted_entries(det: Detected, state: InstallState) -> list[str]:
    """Recorded entries whose real-world counterpart is gone."""
    from forgent import statusline

    missing: list[str] = []
    plugin_scopes = {p["scope"] for p in det.plugin_installs}
    for entry_id, entry in state.entries.items():
        kind = entry.get("kind")
        if kind == "plugin" and entry.get("scope") not in plugin_scopes:
            missing.append(entry_id)
        elif kind == "mcp" and det.mcp_scope != entry.get("scope"):
            missing.append(entry_id)
        elif kind == "marketplace" and not det.marketplace_known:
            missing.append(entry_id)
        elif kind == "statusline":
            try:
                if not statusline.is_installed(entry.get("scope", "user")):
                    missing.append(entry_id)
            except ValueError:
                pass
    return missing


def plan_repair(det: Detected, state: InstallState) -> Plan:
    """Re-apply drifted entries exactly as they were recorded."""
    plan = Plan()
    claude = det.claude_cli
    drift = set(drifted_entries(det, state))
    for entry_id in sorted(drift):
        entry = state.entries[entry_id]
        kind, scope = entry.get("kind"), entry.get("scope", "user")
        if kind == "marketplace" and claude:
            plan.actions.append(Action(entry_id, f"Re-add marketplace `{MARKETPLACE_NAME}`",
                                       argv=[claude, "plugin", "marketplace", "add",
                                             entry.get("source", MARKETPLACE_SOURCE)],
                                       record=entry))
        elif kind == "plugin" and claude:
            plan.actions.append(Action(entry_id, f"Reinstall plugin {PLUGIN_ID} ({scope})",
                                       argv=[claude, "plugin", "install", PLUGIN_ID, "--scope", scope],
                                       record=entry))
        elif kind == "mcp" and claude:
            command = det.forgent_mcp or entry.get("command") or "forgent-mcp"
            plan.actions.append(Action(entry_id, f"Re-register MCP server ({scope})",
                                       argv=[claude, "mcp", "add", "--scope", scope, MCP_NAME, "--", command],
                                       record={**entry, "command": command}))
        elif kind == "statusline":
            plan.actions.append(Action(entry_id, f"Reinstall status line ({scope})",
                                       apply=lambda s=scope: _install_statusline(s), record=entry))
    if not claude and drift:
        plan.blockers.append("Claude Code CLI not on PATH; cannot repair plugin/MCP entries.")
    return plan
