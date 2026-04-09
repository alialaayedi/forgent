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
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import Registry, Ecosystem

load_dotenv()

app = typer.Typer(help="Meta-orchestrator routing tasks across the best agents from across the AI ecosystem.")
agents_app = typer.Typer(help="Inspect the curated agent registry.")
memory_app = typer.Typer(help="Inspect and query the memory store.")
app.add_typer(agents_app, name="agents")
app.add_typer(memory_app, name="memory")

console = Console()


def _db_path() -> str:
    return os.environ.get("FORGENT_DB", "./forgent.db")


@app.command()
def run(
    task: str = typer.Argument(..., help="The task to run, in plain English."),
    show_decision: bool = typer.Option(True, help="Print the routing decision before output."),
    auto_forge: bool = typer.Option(False, "--auto-forge", help="Synthesize a fresh specialist if router confidence is low."),
):
    """Run a task end-to-end: route -> dispatch -> remember."""
    orch = Orchestrator(db_path=_db_path())
    console.print(Panel(task, title="Task", border_style="cyan"))
    result = orch.run(task, auto_forge=auto_forge)
    if show_decision:
        d = result.decision
        console.print(
            Panel(
                f"primary:    [bold]{d.primary}[/bold]\n"
                f"supporting: {', '.join(d.supporting) or '—'}\n"
                f"mode:       {d.mode}\n"
                f"confidence: {d.confidence:.2f}\n"
                f"reasoning:  {d.reasoning}",
                title="Routing decision",
                border_style="magenta",
            )
        )
    style = "green" if result.success else "red"
    body = result.output or "(no output)"
    errors = [f"[{r.agent}] {r.error}" for r in result.results if r.error]
    if errors:
        body = body + "\n\n[bold red]Errors:[/bold red]\n" + "\n".join(errors)
    console.print(Panel(body, title="Result", border_style=style))
    console.print(f"[dim]session_id={result.session_id}[/dim]")


@agents_app.command("list")
def agents_list(
    ecosystem: Optional[str] = typer.Option(None, help="Filter by ecosystem"),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
):
    """List the curated agents in the registry."""
    reg = Registry.load()
    eco = Ecosystem(ecosystem) if ecosystem else None
    agents = reg.filter(ecosystem=eco, category=category)
    table = Table(title=f"Curated agents ({len(agents)} of {len(reg)})", show_lines=False)
    table.add_column("Name", style="cyan")
    table.add_column("Ecosystem", style="magenta")
    table.add_column("Category", style="yellow")
    table.add_column("Description")
    for a in agents:
        table.add_row(a.name, a.ecosystem.value, a.category, a.description)
    console.print(table)


@agents_app.command("search")
def agents_search(query: str = typer.Argument(...)):
    """Keyword search the registry."""
    reg = Registry.load()
    matches = reg.search(query, limit=10)
    if not matches:
        console.print(f"[yellow]No agents matched '{query}'[/yellow]")
        return
    table = Table(title=f"Top matches for '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Description")
    for a in matches:
        table.add_row(a.name, str(a.matches(query)), a.description)
    console.print(table)


@agents_app.command("show")
def agents_show(name: str = typer.Argument(...)):
    """Print the full system prompt for a curated agent."""
    reg = Registry.load()
    agent = reg.get(name)
    if agent is None:
        console.print(f"[red]No agent named '{name}'[/red]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"ecosystem:    {agent.ecosystem.value}\n"
            f"category:     {agent.category}\n"
            f"capabilities: {', '.join(agent.capabilities)}\n"
            f"source:       {agent.source_repo}/{agent.source_path}\n"
            f"model:        {agent.model or '(inherit)'}",
            title=agent.name,
            border_style="cyan",
        )
    )
    body = agent.load_body()
    if body:
        console.print(Panel(body[:4000] + ("\n…" if len(body) > 4000 else ""), title="System prompt"))
    else:
        console.print("[yellow]Body not yet vendored — run `orchestrator vendor`[/yellow]")


@memory_app.command("stats")
def memory_stats():
    """Show counts of what's in memory."""
    mem = MemoryStore(_db_path())
    stats = mem.stats()
    if not stats:
        console.print("[yellow]Memory is empty — run a task first[/yellow]")
        return
    table = Table(title="Memory store contents")
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right")
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
        console.print(f"[yellow]Nothing recalled for '{query}'[/yellow]")
        return
    for e in entries:
        console.print(
            Panel(
                e.content[:2000] + ("\n…" if len(e.content) > 2000 else ""),
                title=f"{e.type.value} | {', '.join(e.tags) or '—'}",
                border_style="dim",
            )
        )


@memory_app.command("forget")
def memory_forget():
    """Wipe the memory store. Asks for confirmation."""
    path = Path(_db_path())
    if not path.exists():
        console.print("[yellow]No memory store to forget[/yellow]")
        return
    if not typer.confirm(f"Delete {path}?"):
        return
    path.unlink()
    console.print(f"[green]Deleted {path}[/green]")


@app.command()
def vendor(force: bool = typer.Option(False, help="Overwrite existing vendored files")):
    """Copy source files from sources/ into the registry — makes the project self-contained."""
    reg = Registry.load()
    copied, skipped = reg.vendor(force=force)
    console.print(f"[green]Vendored {copied} agents[/green] (skipped {skipped})")


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
    status = "[green]new[/green]" if forged.is_new else "[yellow]reused existing[/yellow]"
    console.print(
        Panel(
            f"name:         {spec.name} ({status})\n"
            f"category:     {spec.category}\n"
            f"capabilities: {', '.join(spec.capabilities)}\n"
            f"model:        {spec.model}\n"
            f"description:  {spec.description}",
            title="Forged agent",
            border_style="magenta",
        )
    )
    body = forged.body or spec.load_body()
    if body:
        snippet = body[:1500] + ("\n..." if len(body) > 1500 else "")
        console.print(Panel(snippet, title="System prompt (first 1500 chars)"))
    console.print("[dim]Persisted to dynamic.yaml + agents/claude_code/. Available immediately.[/dim]")


@app.command()
def stats():
    """High-level overview: how many agents, how many sessions, what's in memory."""
    reg = Registry.load()
    mem = MemoryStore(_db_path())
    eco_counts: dict[str, int] = {}
    for a in reg:
        eco_counts[a.ecosystem.value] = eco_counts.get(a.ecosystem.value, 0) + 1
    table = Table(title="Forgent overview")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("total curated agents", str(len(reg)))
    for eco, n in sorted(eco_counts.items()):
        table.add_row(f"  {eco}", str(n))
    table.add_row("categories", str(len(reg.categories())))
    for k, v in mem.stats().items():
        table.add_row(f"memory: {k}", str(v))
    console.print(table)


if __name__ == "__main__":
    app()
