"""Typer CLI — the user-facing entry point.

Commands:
    orchestrator run "<task>"             — execute a task
    orchestrator agents list              — list curated agents
    orchestrator agents search "<query>"  — find agents by keyword
    orchestrator memory stats             — show what's in the memory store
    orchestrator memory recall "<query>"  — pull relevant past context
    orchestrator vendor                   — copy source files into the registry
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
from forgent.progress import cli_progress
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
def run(
    task: str = typer.Argument(..., help="The task to run, in plain English."),
    show_decision: bool = typer.Option(True, help="Print the routing decision before output."),
    auto_forge: bool = typer.Option(False, "--auto-forge", help="Synthesize a fresh specialist if router confidence is low."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Disable the live progress spinner."),
):
    """Run a task end-to-end: route -> dispatch -> remember."""
    orch = Orchestrator(db_path=_db_path())
    console.print(Panel(task, title="[title]task[/title]", border_style=COLORS.border_strong))

    if quiet:
        result = orch.run(task, auto_forge=auto_forge)
    else:
        with cli_progress(console=console) as progress:
            result = orch.run(task, auto_forge=auto_forge, progress=progress)

    if show_decision:
        d = result.decision
        console.print(
            Panel(
                f"[label]primary[/label]    [accent]{d.primary}[/accent]\n"
                f"[label]supporting[/label] [secondary]{', '.join(d.supporting) or '—'}[/secondary]\n"
                f"[label]mode[/label]       [secondary]{d.mode}[/secondary]\n"
                f"[label]confidence[/label] [secondary]{d.confidence:.2f}[/secondary]\n"
                f"[label]reasoning[/label]  [muted]{d.reasoning}[/muted]",
                title="[subtitle]routing decision[/subtitle]",
                border_style=COLORS.border,
            )
        )
    border = "green" if result.success else COLORS.accent
    body = result.output or "[muted](no output)[/muted]"
    errors = [f"[{r.agent}] {r.error}" for r in result.results if r.error]
    if errors:
        body = body + "\n\n[error]errors:[/error]\n" + "\n".join(errors)
    console.print(Panel(body, title="[title]result[/title]", border_style=border))
    console.print(f"[muted]session_id={result.session_id}[/muted]")


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
