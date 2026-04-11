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

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import Registry, Ecosystem
from forgent.theme import COLORS, console

load_dotenv()

app = typer.Typer(help="forgent — grow your own AI subagents on demand. Routes any task to the best curated agent across Claude Code subagents, Python frameworks, and MCP servers.")
agents_app = typer.Typer(help="Inspect the curated agent registry.")
memory_app = typer.Typer(help="Inspect and query the memory store.")
app.add_typer(agents_app, name="agents")
app.add_typer(memory_app, name="memory")


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


if __name__ == "__main__":
    app()
