"""Typer CLI — the user-facing entry point.

Commands:
    forgent advise "<task>"            — build a PlanCard (plan, gotchas, success)
    forgent agents list                — list curated agents
    forgent agents search "<query>"    — find agents by keyword
    forgent memory stats               — show what's in the memory store
    forgent memory recall "<query>"    — pull relevant past context
    forgent vendor                     — copy source files into the registry

forgent is a planning + knowledge layer: `advise` returns a structured plan
card for the host LLM to execute with its own tools. It does not run agents
itself -- that was the v1 model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.panel import Panel
from rich.table import Table

from forgent.config import ForgentConfig
from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent import statusline as statusline_mod
from forgent.registry.loader import Registry, Ecosystem
from forgent.theme import COLORS, console

load_dotenv()

app = typer.Typer(help="forgent — grow your own AI subagents on demand. Routes any task to the best curated agent across Claude Code subagents, Python frameworks, and MCP servers.")
agents_app = typer.Typer(help="Inspect the curated agent registry.")
memory_app = typer.Typer(help="Inspect and query the memory store.")
statusline_app = typer.Typer(help="Manage the optional forgent status line for Claude Code.")
app.add_typer(agents_app, name="agents")
app.add_typer(memory_app, name="memory")
app.add_typer(statusline_app, name="statusline")


def _db_path() -> str:
    return os.environ.get("FORGENT_DB", "./forgent.db")


@app.command()
def advise(
    task: str = typer.Argument(..., help="The task to plan, in plain English."),
    auto_forge: bool = typer.Option(True, "--auto-forge/--no-forge", help="Synthesize a fresh knowledge pack if router confidence is low."),
):
    """Plan a task: route -> recall memory -> build a PlanCard.

    Returns a structured plan card with steps, gotchas, success criteria, and
    recalled memory. Execute the plan yourself (or pipe it to a coding agent
    via MCP) and then call `forgent outcome` to close the feedback loop.
    """
    orch = Orchestrator(db_path=_db_path())
    console.print(Panel(task, title="[title]task[/title]", border_style=COLORS.border_strong))

    plan = orch.advise(task, auto_forge=auto_forge)

    # Routing pane
    sup = ", ".join(plan.supporting) or "—"
    mode_tag = " (heuristic)" if plan.heuristic else ""
    console.print(
        Panel(
            f"[label]knowledge[/label]   [accent]{plan.primary_agent}[/accent]\n"
            f"[label]supporting[/label]  [secondary]{sup}[/secondary]\n"
            f"[label]confidence[/label]  [secondary]{plan.confidence:.2f}{mode_tag}[/secondary]\n"
            f"[label]reason[/label]      [muted]{plan.routing_reasoning}[/muted]",
            title="[subtitle]plan card[/subtitle]",
            border_style=COLORS.border,
        )
    )

    if plan.knowledge_pack_summary:
        console.print(
            Panel(
                plan.knowledge_pack_summary,
                title="[subtitle]knowledge synthesis[/subtitle]",
                border_style=COLORS.border,
            )
        )

    if plan.steps:
        steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan.steps))
        console.print(
            Panel(steps_text, title="[subtitle]plan[/subtitle]", border_style=COLORS.border)
        )

    if plan.gotchas:
        gotchas_text = "\n".join(f"- {g}" for g in plan.gotchas)
        console.print(
            Panel(gotchas_text, title="[subtitle]gotchas[/subtitle]", border_style=COLORS.accent)
        )

    if plan.success_criteria:
        sc_text = "\n".join(f"- {c}" for c in plan.success_criteria)
        console.print(
            Panel(sc_text, title="[subtitle]success criteria[/subtitle]", border_style=COLORS.border)
        )

    if plan.past_outcomes:
        outcomes_text = "\n".join(f"- {o}" for o in plan.past_outcomes)
        console.print(
            Panel(
                outcomes_text,
                title="[subtitle]past outcomes on similar tasks[/subtitle]",
                border_style=COLORS.border,
            )
        )

    console.print(f"[muted]session_id={plan.session_id}[/muted]")
    console.print(
        f"[muted]after execution, run:[/muted] [accent]forgent outcome {plan.session_id[:8]}"
        f" --success/--failure \"notes\"[/accent]"
    )


@app.command()
def outcome(
    session_id: str = typer.Argument(..., help="Session id from a prior `advise` call."),
    success: bool = typer.Option(True, "--success/--failure", help="Did the task complete successfully?"),
    notes: str = typer.Option("", "--notes", help="Optional free-form notes about what happened."),
    agent: Optional[str] = typer.Option(None, "--agent", help="Agent/knowledge pack name this outcome is for."),
):
    """Record whether a planned task succeeded. Feeds routing for next time."""
    orch = Orchestrator(db_path=_db_path())
    orch.record_outcome(session_id=session_id, success=success, notes=notes, agent_name=agent)
    status = "[success]success[/success]" if success else "[error]failure[/error]"
    console.print(f"[muted]outcome recorded for[/muted] [accent]{session_id[:8]}[/accent]: {status}")


@agents_app.command("list")
def agents_list(
    ecosystem: Optional[str] = typer.Option(None, help="Filter by ecosystem"),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
):
    """List the curated agents in the registry."""
    reg = Registry.load()
    eco = Ecosystem(ecosystem) if ecosystem else None
    agents = reg.filter(ecosystem=eco, category=category)
    table = Table(
        title=f"[title]curated agents[/title] [muted]({len(agents)} of {len(reg)})[/muted]",
        show_lines=False,
        border_style=COLORS.border,
        header_style=f"bold {COLORS.accent}",
    )
    table.add_column("name", style=COLORS.accent)
    table.add_column("ecosystem", style=COLORS.fg_secondary)
    table.add_column("category", style=COLORS.fg_secondary)
    table.add_column("description", style=COLORS.fg)
    for a in agents:
        table.add_row(a.name, a.ecosystem.value, a.category, a.description)
    console.print(table)


@agents_app.command("search")
def agents_search(query: str = typer.Argument(...)):
    """Keyword search the registry."""
    reg = Registry.load()
    matches = reg.search(query, limit=10)
    if not matches:
        console.print(f"[warning]no agents matched '{query}'[/warning]")
        return
    table = Table(
        title=f"[title]top matches for '{query}'[/title]",
        border_style=COLORS.border,
        header_style=f"bold {COLORS.accent}",
    )
    table.add_column("name", style=COLORS.accent)
    table.add_column("score", justify="right", style=COLORS.fg_secondary)
    table.add_column("description", style=COLORS.fg)
    for a in matches:
        table.add_row(a.name, str(a.matches(query)), a.description)
    console.print(table)


@agents_app.command("show")
def agents_show(name: str = typer.Argument(...)):
    """Print the full system prompt for a curated agent."""
    reg = Registry.load()
    agent = reg.get(name)
    if agent is None:
        console.print(f"[error]no agent named '{name}'[/error]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"[label]ecosystem[/label]    [secondary]{agent.ecosystem.value}[/secondary]\n"
            f"[label]category[/label]     [secondary]{agent.category}[/secondary]\n"
            f"[label]capabilities[/label] [secondary]{', '.join(agent.capabilities)}[/secondary]\n"
            f"[label]source[/label]       [muted]{agent.source_repo}/{agent.source_path}[/muted]\n"
            f"[label]model[/label]        [secondary]{agent.model or '(inherit)'}[/secondary]",
            title=f"[title]{agent.name}[/title]",
            border_style=COLORS.border_strong,
        )
    )
    body = agent.load_body()
    if body:
        console.print(
            Panel(
                body[:4000] + ("\n…" if len(body) > 4000 else ""),
                title="[subtitle]system prompt[/subtitle]",
                border_style=COLORS.border,
            )
        )
    else:
        console.print("[warning]body not yet vendored — run `forgent vendor`[/warning]")


@memory_app.command("stats")
def memory_stats():
    """Show counts of what's in memory."""
    mem = MemoryStore(_db_path())
    stats = mem.stats()
    if not stats:
        console.print("[muted]memory is empty — run a task first[/muted]")
        return
    table = Table(
        title="[title]memory store contents[/title]",
        border_style=COLORS.border,
        header_style=f"bold {COLORS.accent}",
    )
    table.add_column("type", style=COLORS.accent)
    table.add_column("count", justify="right", style=COLORS.fg)
    for type_, n in sorted(stats.items()):
        table.add_row(type_, str(n))
    console.print(table)


@memory_app.command("recall")
def memory_recall(
    query: str = typer.Argument(...),
    limit: int = typer.Option(5),
    type: Optional[str] = typer.Option(None, help="Filter by memory type"),
):
    """Show what the memory store would recall for a given query."""
    mem = MemoryStore(_db_path())
    mtype = MemoryType(type) if type else None
    entries = mem.recall(query, limit=limit, type=mtype)
    if not entries:
        console.print(f"[muted]nothing recalled for '{query}'[/muted]")
        return
    for e in entries:
        console.print(
            Panel(
                e.content[:2000] + ("\n…" if len(e.content) > 2000 else ""),
                title=f"[subtitle]{e.type.value}[/subtitle] [muted]| {', '.join(e.tags) or '—'}[/muted]",
                border_style=COLORS.border,
            )
        )


@memory_app.command("forget")
def memory_forget():
    """Wipe the memory store. Asks for confirmation."""
    path = Path(_db_path())
    if not path.exists():
        console.print("[muted]no memory store to forget[/muted]")
        return
    if not typer.confirm(f"Delete {path}?"):
        return
    path.unlink()
    console.print(f"[success]deleted {path}[/success]")


@app.command()
def vendor(force: bool = typer.Option(False, help="Overwrite existing vendored files")):
    """Copy source files from sources/ into the registry — makes the project self-contained."""
    reg = Registry.load()
    copied, skipped = reg.vendor(force=force)
    console.print(f"[success]vendored {copied} agents[/success] [muted](skipped {skipped})[/muted]")


@app.command()
def forge(
    task: str = typer.Argument(..., help="What this new specialist should be good at."),
    name: Optional[str] = typer.Option(None, help="Explicit name (otherwise the LLM picks)."),
    category: Optional[str] = typer.Option(None, help="Category hint."),
    force_new: bool = typer.Option(False, "--force", help="Overwrite an existing forged agent with the same name."),
):
    """Synthesize a new specialist subagent and add it to the registry permanently."""
    import asyncio
    orch = Orchestrator(db_path=_db_path())
    forged = asyncio.run(orch.forge_agent(task, name=name, category=category, force=force_new))
    spec = forged.spec
    status = "[success]new[/success]" if forged.is_new else "[muted]reused existing[/muted]"
    console.print(
        Panel(
            f"[label]name[/label]         [accent]{spec.name}[/accent] ({status})\n"
            f"[label]category[/label]     [secondary]{spec.category}[/secondary]\n"
            f"[label]capabilities[/label] [secondary]{', '.join(spec.capabilities)}[/secondary]\n"
            f"[label]model[/label]        [secondary]{spec.model}[/secondary]\n"
            f"[label]description[/label]  [muted]{spec.description}[/muted]",
            title="[title]forged agent[/title]",
            border_style=COLORS.border_strong,
        )
    )
    body = forged.body or spec.load_body()
    if body:
        snippet = body[:1500] + ("\n..." if len(body) > 1500 else "")
        console.print(
            Panel(
                snippet,
                title="[subtitle]system prompt (first 1500 chars)[/subtitle]",
                border_style=COLORS.border,
            )
        )
    console.print("[muted]persisted to dynamic.yaml + agents/claude_code/. available immediately.[/muted]")


@app.command()
def verify(
    detectors: Optional[str] = typer.Option(None, "--only", help="Comma-separated detector names to run. Default: all (git_diff,tests,lint,ci)."),
):
    """Run forgent's outcome verifier in the current working directory.

    Runs each detector in parallel: git_diff (did files change?), tests (run
    the project test suite), lint (lint the working tree), ci (check the
    latest GitHub Actions run). Returns evidence-backed pass/fail/skip per
    detector. Skipped detectors are those that can't run in this cwd (no
    test runner detected, etc.) and don't affect the verdict.
    """
    from forgent.verify import Verifier
    subset = [n.strip() for n in detectors.split(",")] if detectors else None
    result = Verifier().run(os.getcwd(), subset=subset)
    rows: list[str] = []
    for r in result.ran + result.skipped:
        icon = {"pass": "[success]ok[/success]", "fail": "[error]FAIL[/error]", "unknown": "[muted]skip[/muted]"}[r.status]
        rows.append(f"{icon} [label]{r.name}[/label] [muted]({r.duration_ms}ms)[/muted] -- {r.evidence}")
    verdict = "[success]PASS[/success]" if result.success else "[error]FAIL[/error]"
    console.print(
        Panel(
            "\n".join(rows) + f"\n\n[label]verdict[/label]  {verdict}\n{result.to_summary()}",
            title="[title]verifier[/title]",
            border_style=COLORS.border_strong,
        )
    )
    if not result.success:
        raise typer.Exit(1)


@app.command()
def autocompact(
    pct: str = typer.Argument(..., help="Percent (1-99) or 'reset' to remove the override."),
    scope: str = typer.Option("user", "--scope", help="Where to write: 'user' (~/.claude) or 'project' (./.claude)."),
):
    """Set Claude Code's auto-compact threshold via CLAUDE_AUTOCOMPACT_PCT_OVERRIDE.

    Claude Code's default threshold is ~92%, meaning conversations rarely get
    compacted until context is almost full. Lowering it (e.g. to 60%) makes
    Claude Code compact sooner, keeping answers crisp across longer sessions.

    This writes to ~/.claude/settings.json (or ./.claude/settings.json with
    --scope=project) and aligns forgent's own compact countdown in the
    status line.
    """
    if scope not in ("user", "project"):
        console.print("[error]scope must be 'user' or 'project'[/error]")
        raise typer.Exit(2)
    if pct.lower() in ("reset", "off", "none"):
        path = statusline_mod.set_autocompact(None, scope=scope)
        console.print(
            f"[success]auto-compact override removed[/success] "
            f"[muted]({path})[/muted]"
        )
        return
    try:
        n = int(pct)
    except ValueError:
        console.print(f"[error]invalid value '{pct}' -- expected 1-99 or 'reset'[/error]")
        raise typer.Exit(2)
    if not (1 <= n <= 99):
        console.print("[error]pct must be between 1 and 99[/error]")
        raise typer.Exit(2)
    path = statusline_mod.set_autocompact(n, scope=scope)
    warn = ""
    if n < 40:
        warn = "\n[warning]heads-up: below 40% means very frequent compaction[/warning]"
    elif n > 90:
        warn = "\n[warning]heads-up: above 90% is close to default -- little effect[/warning]"
    console.print(
        Panel(
            f"[label]CLAUDE_AUTOCOMPACT_PCT_OVERRIDE[/label]  [secondary]{n}[/secondary]\n"
            f"[label]settings.json[/label]  [muted]{path}[/muted]\n\n"
            "[muted]Restart Claude Code to pick up the new threshold.[/muted]"
            f"{warn}",
            title="[title]auto-compact[/title]",
            border_style=COLORS.border_strong,
        )
    )


@app.command()
def stats():
    """High-level overview: how many agents, how many sessions, what's in memory."""
    reg = Registry.load()
    mem = MemoryStore(_db_path())
    eco_counts: dict[str, int] = {}
    for a in reg:
        eco_counts[a.ecosystem.value] = eco_counts.get(a.ecosystem.value, 0) + 1
    table = Table(
        title="[title]forgent overview[/title]",
        border_style=COLORS.border,
        header_style=f"bold {COLORS.accent}",
    )
    table.add_column("metric", style=COLORS.accent)
    table.add_column("value", justify="right", style=COLORS.fg)
    table.add_row("total curated agents", str(len(reg)))
    for eco, n in sorted(eco_counts.items()):
        table.add_row(f"  {eco}", str(n))
    table.add_row("categories", str(len(reg.categories())))
    for k, v in mem.stats().items():
        table.add_row(f"memory: {k}", str(v))
    console.print(table)


@statusline_app.command("enable")
def statusline_enable(
    scope: str = typer.Option("user", "--scope", help="Where to install: 'user' (~/.claude) or 'project' (./.claude)."),
    autocompact: int = typer.Option(60, "--autocompact", help="Also set Claude Code's auto-compact threshold (1-99). Use 0 to skip."),
):
    """Enable the forgent status line and wire it into Claude Code settings."""
    if scope not in ("user", "project"):
        console.print("[error]scope must be 'user' or 'project'[/error]")
        raise typer.Exit(2)
    autocompact_pct = autocompact if autocompact else None
    path = statusline_mod.install(scope=scope, autocompact_pct=autocompact_pct)
    cfg = ForgentConfig.load()
    cfg.record_statusline_choice("accepted")
    extra_line = (
        f"[label]auto-compact[/label]  [secondary]{autocompact}% (via CLAUDE_AUTOCOMPACT_PCT_OVERRIDE)[/secondary]\n"
        if autocompact_pct
        else ""
    )
    console.print(
        Panel(
            f"[success]enabled[/success] [muted]({scope} scope)[/muted]\n"
            f"[label]settings.json[/label]  [secondary]{path}[/secondary]\n"
            f"[label]command[/label]        [secondary]forgent-statusline[/secondary]\n"
            f"{extra_line}\n"
            "[muted]Restart Claude Code to see the new status line.[/muted]",
            title="[title]forgent status line[/title]",
            border_style=COLORS.border_strong,
        )
    )


@statusline_app.command("decline")
def statusline_decline():
    """Record that the status line should NOT be enabled. The first-run banner will never show again."""
    cfg = ForgentConfig.load()
    cfg.record_statusline_choice("declined")
    console.print(
        "[muted]ok -- forgent won't offer the status line again. "
        "Run `forgent statusline enable` later if you change your mind.[/muted]"
    )


@statusline_app.command("disable")
def statusline_disable(
    scope: str = typer.Option("user", "--scope", help="Where to uninstall from: 'user' or 'project'."),
):
    """Remove the forgent status line from Claude Code settings (keeps consent choice)."""
    if scope not in ("user", "project"):
        console.print("[error]scope must be 'user' or 'project'[/error]")
        raise typer.Exit(2)
    changed = statusline_mod.uninstall(scope=scope)
    if changed:
        console.print(f"[success]removed forgent status line from {scope} settings[/success]")
    else:
        console.print(f"[muted]no forgent status line to remove in {scope} settings[/muted]")


@statusline_app.command("show")
def statusline_show():
    """Render the status line locally (useful for previewing what Claude Code will show)."""
    import os as _os
    ctx = {
        "cwd": _os.getcwd(),
        "model": {"id": "claude-opus-4-7", "display_name": "Opus 4.7"},
    }
    line = statusline_mod.render_line(ctx)
    console.print(line)


@statusline_app.command("preview")
def statusline_preview(
    mode: Optional[str] = typer.Option(None, "--mode", help="Preview a specific mode: minimal | powerline | capsule | compact. Omit to show all."),
    theme: Optional[str] = typer.Option(None, "--theme", help="Preview with a specific theme: dark | light | highcontrast."),
):
    """Preview the status line locally with realistic fake data. Doesn't touch settings."""
    import json as _json
    import time as _time
    ctx = {
        "cwd": os.getcwd(),
        "model": {"id": "claude-opus-4-7", "display_name": "Opus 4.7"},
        "session_id": "preview-session-abcdef",
        "cost": {"total_cost_usd": 0.27},
        "rate_limits": {"five_hour": {"used_percentage": 23.5}},
        "context_window": {"used_percentage": 38, "context_window_size": 1_000_000},
    }
    modes = [mode] if mode else ["minimal", "powerline", "capsule", "compact"]
    for m in modes:
        console.print(f"\n[subtitle]{m}[/subtitle]")
        line = statusline_mod.render_line(ctx, mode=m, theme_name=theme, width=200)
        # Rich doesn't render raw ANSI in Panel; print directly.
        import sys as _sys
        _sys.stdout.write(line + "\n")


@statusline_app.command("status")
def statusline_status():
    """Report current consent + install state."""
    cfg = ForgentConfig.load()
    choice = cfg.statusline_choice() or "(not decided)"
    prompted = "yes" if cfg.consent_prompted() else "no"
    user_on = statusline_mod.is_installed("user")
    project_on = statusline_mod.is_installed("project")
    console.print(
        Panel(
            f"[label]consent[/label]          [secondary]{choice}[/secondary]\n"
            f"[label]banner shown[/label]     [secondary]{prompted}[/secondary]\n"
            f"[label]user scope[/label]       [secondary]{'installed' if user_on else 'not installed'}[/secondary]\n"
            f"[label]project scope[/label]    [secondary]{'installed' if project_on else 'not installed'}[/secondary]\n"
            f"[label]config file[/label]      [muted]{cfg.path}[/muted]",
            title="[title]forgent status line[/title]",
            border_style=COLORS.border,
        )
    )


# ---------------------------------------------------------------------------
# Marketplace / IDE / team / evals (v0.4 scaffolds)
# ---------------------------------------------------------------------------


@app.command()
def install(name_or_url: str = typer.Argument(..., help="Known pack name (wshobson-agents, voltagent, furai) or a git URL.")):
    """Install a community agent pack into forgent's registry.

    Examples:
        forgent install wshobson-agents
        forgent install https://github.com/some-user/cool-agents
    """
    from forgent.marketplace import install as do_install, KNOWN_PACKS
    try:
        result = do_install(name_or_url)
    except Exception as exc:
        console.print(f"[error]install failed:[/error] {exc}")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"[label]pack[/label]          [accent]{result.pack_name}[/accent]\n"
            f"[label]source[/label]        [muted]{result.source_url}[/muted]\n"
            f"[label]agents added[/label]  [secondary]{result.agents_added}[/secondary]\n"
            f"[label]destination[/label]   [muted]{result.destination}[/muted]\n\n"
            "[muted]Run `forgent agents list` to see them.[/muted]",
            title="[title]pack installed[/title]",
            border_style=COLORS.border_strong,
        )
    )


@app.command("setup-ide")
def setup_ide(
    editor: str = typer.Argument(..., help="claude-code | claude-desktop | cursor | cline | roo | zed | continue"),
):
    """Print the MCP config snippet for an editor. No files written."""
    from forgent.ide_setup import snippet_for
    try:
        snip = snippet_for(editor)
    except ValueError as exc:
        console.print(f"[error]{exc}[/error]")
        raise typer.Exit(2)
    console.print(
        Panel(
            f"[label]editor[/label]      [accent]{snip.editor}[/accent]\n"
            f"[label]config file[/label] [muted]{snip.config_path}[/muted]\n"
            f"[label]format[/label]      [secondary]{snip.format}[/secondary]\n\n"
            f"[muted]{snip.notes}[/muted]",
            title="[title]forgent + MCP[/title]",
            border_style=COLORS.border_strong,
        )
    )
    console.print("\n[subtitle]paste this[/subtitle]")
    import sys as _sys
    _sys.stdout.write(snip.snippet + "\n")


team_app = typer.Typer(help="Team-scoped memory (scaffolded for v0.4, full RBAC deferred).")
app.add_typer(team_app, name="team")


@team_app.command("init")
def team_init(name: str = typer.Argument(..., help="Team identifier (free-form slug).")):
    """Tag all future memory writes with a team_id.

    Current implementation is local-only: you share the forgent.db file across
    the team. Real authenticated multi-team RBAC requires a backend server
    and is on the ROADMAP. Until then, treat team_id as a soft scope tag.
    """
    cfg = ForgentConfig.load()
    cfg.set_team_id(name)
    console.print(f"[success]team set to[/success] [accent]{name}[/accent]")
    console.print("[muted]New memory writes will carry team_id='" + name + "'[/muted]")


@team_app.command("clear")
def team_clear():
    """Unset the team_id."""
    cfg = ForgentConfig.load()
    cfg.set_team_id(None)
    console.print("[success]team cleared[/success]")


eval_app = typer.Typer(help="Plan-quality evaluations (v0.4 local scaffold).")
app.add_typer(eval_app, name="eval")


@eval_app.command("list")
def eval_list():
    """List bundled benchmarks."""
    console.print(
        Panel(
            "[label]swe-bench-lite[/label] [muted]-- 300-task subset of SWE-bench; compares forgent plans to known-good diffs[/muted]\n\n"
            "[muted]Full benchmarks require cloning the datasets. Public leaderboard is on the ROADMAP.[/muted]",
            title="[title]forgent evals[/title]",
            border_style=COLORS.border,
        )
    )


@eval_app.command("run")
def eval_run(
    benchmark: str = typer.Argument("swe-bench-lite"),
    limit: int = typer.Option(10, "--limit", help="How many tasks to run."),
):
    """Run a benchmark locally. Scaffold: prints what WILL run and exits.

    The full runner clones the benchmark dataset, iterates tasks, calls
    `forgent advise` on each, and writes a JSON report. That's a bigger
    lift than fits in v0.4 -- this stub validates the plumbing.
    """
    console.print(
        Panel(
            f"[label]benchmark[/label]  [accent]{benchmark}[/accent]\n"
            f"[label]tasks[/label]      [secondary]{limit}[/secondary]\n\n"
            "[warning]scaffold only -- dataset-loading runner arrives in a follow-up.[/warning]\n"
            "[muted]See ROADMAP.md for the full spec.[/muted]",
            title="[title]eval run[/title]",
            border_style=COLORS.accent,
        )
    )


if __name__ == "__main__":
    app()
