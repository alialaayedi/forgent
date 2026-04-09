"""MCP server that exposes the orchestrator as a set of tools.

Run as a stdio MCP server (the only transport Claude Desktop and Claude Code
support today). Register it with:

    claude mcp add forgent -- /abs/path/to/.venv/bin/forgent-mcp

Or in Claude Desktop's claude_desktop_config.json:

    {
      "mcpServers": {
        "forgent": {
          "command": "/abs/path/to/.venv/bin/forgent-mcp",
          "env": {
            "ANTHROPIC_API_KEY": "sk-ant-...",
            "FORGENT_DB": "./forgent.db"
          }
        }
      }
    }

Tools exposed (any MCP client can call these):

    run_task         — full orchestrator run: route → dispatch → remember
    list_agents      — list curated agents (optional ecosystem/category filter)
    search_agents    — keyword search the registry
    show_agent       — full system prompt + metadata for one agent
    recall_memory    — query the memory store by keyword
    memory_stats     — counts by type
    route_only       — return the routing decision without executing (cheap)

Per-project memory: the server reads FORGENT_DB from the env, falling
back to ./forgent.db relative to wherever the MCP client launched the
server. Different working directories get different memory stores.
"""

from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import Ecosystem, Registry

mcp = FastMCP("forgent")

# Lazy singletons — built on first tool call so server startup is instant.
_registry: Optional[Registry] = None
_orchestrator: Optional[Orchestrator] = None


def _db_path() -> str:
    return os.environ.get("FORGENT_DB", "./forgent.db")


def _get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry.load()
    return _registry


def _get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(registry=_get_registry(), db_path=_db_path())
    return _orchestrator


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_task(task: str, auto_forge: bool = False) -> str:
    """Run a task end-to-end through the orchestrator.

    Routes the task to the best curated agent (or set of agents), executes
    them via the matching ecosystem adapter, persists everything to the
    project-local memory store, and returns the merged output along with the
    routing decision.

    Args:
        task: The task to perform, in plain English.
        auto_forge: If true, synthesizes a brand-new specialist subagent when
            the router's confidence in the existing catalog is low. The new
            agent is persisted and reused for future tasks of the same shape.

    Returns:
        A formatted string with the routing decision and the agent output(s).
    """
    orch = _get_orchestrator()
    result = await orch.run_async(task, auto_forge=auto_forge)
    d = result.decision
    header = (
        f"## Routing decision\n"
        f"- primary: {d.primary}\n"
        f"- supporting: {', '.join(d.supporting) or '(none)'}\n"
        f"- mode: {d.mode}\n"
        f"- confidence: {d.confidence:.2f}\n"
        f"- reasoning: {d.reasoning}\n"
        f"- session: {result.session_id}\n"
    )
    if not result.success:
        errors = "\n".join(f"- [{r.agent}] {r.error}" for r in result.results if r.error)
        return f"{header}\n## Errors\n{errors}\n\n## Partial output\n{result.output}"
    return f"{header}\n## Output\n{result.output}"


@mcp.tool()
def list_agents(ecosystem: Optional[str] = None, category: Optional[str] = None) -> str:
    """List curated agents in the registry.

    Args:
        ecosystem: Optional filter — one of "claude_code", "python_framework", "mcp".
        category: Optional category filter (e.g. "core-development", "data-ai").

    Returns:
        A markdown table of matching agents.
    """
    reg = _get_registry()
    eco = Ecosystem(ecosystem) if ecosystem else None
    agents = reg.filter(ecosystem=eco, category=category)
    if not agents:
        return f"(no agents matched filters ecosystem={ecosystem}, category={category})"
    lines = ["| name | ecosystem | category | description |", "|---|---|---|---|"]
    for a in agents:
        desc = a.description.replace("|", "\\|")
        lines.append(f"| {a.name} | {a.ecosystem.value} | {a.category} | {desc} |")
    lines.append(f"\n_{len(agents)} of {len(reg)} curated agents._")
    return "\n".join(lines)


@mcp.tool()
def search_agents(query: str, limit: int = 10) -> str:
    """Keyword-search the agent registry.

    Args:
        query: Free-text query (e.g. "kubernetes security", "stripe webhook").
        limit: Max results to return.

    Returns:
        Markdown list of top matches with relevance scores.
    """
    reg = _get_registry()
    matches = reg.search(query, limit=limit)
    if not matches:
        return f"No agents matched '{query}'."
    lines = [f"# Top matches for '{query}'", ""]
    for a in matches:
        lines.append(
            f"- **{a.name}** (score {a.matches(query)}, {a.ecosystem.value}/{a.category}) — {a.description}"
        )
    return "\n".join(lines)


@mcp.tool()
def show_agent(name: str) -> str:
    """Return the full system prompt and metadata for a curated agent.

    Args:
        name: The agent's name from the registry (e.g. "backend-developer").

    Returns:
        A markdown block with metadata followed by the system prompt.
    """
    reg = _get_registry()
    agent = reg.get(name)
    if agent is None:
        return f"No agent named '{name}'. Try `list_agents` or `search_agents`."
    body = agent.load_body() or "(body not vendored — run `orchestrator vendor`)"
    return (
        f"# {agent.name}\n\n"
        f"- ecosystem: {agent.ecosystem.value}\n"
        f"- category: {agent.category}\n"
        f"- capabilities: {', '.join(agent.capabilities)}\n"
        f"- source: {agent.source_repo}/{agent.source_path}\n"
        f"- model: {agent.model or '(inherit)'}\n\n"
        f"## System prompt\n\n{body}"
    )


@mcp.tool()
def recall_memory(query: str, limit: int = 5, type: Optional[str] = None) -> str:
    """Query the project-local memory store via keyword recall.

    Args:
        query: The keywords to search for.
        limit: Max entries to return.
        type: Optional memory type filter — one of "task", "routing",
              "agent_output", "decision", "agent_doc", "note", "artifact".

    Returns:
        Markdown-formatted recall results.
    """
    mem = MemoryStore(_db_path())
    mtype = MemoryType(type) if type else None
    entries = mem.recall(query, limit=limit, type=mtype)
    if not entries:
        return f"Nothing recalled for '{query}'."
    lines = [f"# Recalled for '{query}'", ""]
    for e in entries:
        snippet = e.content if len(e.content) <= 800 else e.content[:800] + "…"
        tag_str = f" [{', '.join(e.tags)}]" if e.tags else ""
        lines.append(f"## {e.type.value}{tag_str}\n{snippet}\n")
    return "\n".join(lines)


@mcp.tool()
def memory_stats() -> str:
    """Show counts of what's currently stored in the project's memory."""
    mem = MemoryStore(_db_path())
    stats = mem.stats()
    if not stats:
        return f"Memory store at {_db_path()} is empty."
    lines = [f"# Memory store: {_db_path()}", ""]
    for type_, n in sorted(stats.items()):
        lines.append(f"- {type_}: {n}")
    return "\n".join(lines)


@mcp.tool()
async def forge_agent(
    task: str,
    name: Optional[str] = None,
    category: Optional[str] = None,
    force: bool = False,
) -> str:
    """Synthesize a new specialist subagent for a task class.

    The orchestrator can grow its own agents on demand. Use this when no
    existing agent fits a recurring task you care about — the forged agent
    is persisted to disk and becomes available to every future call of
    `run_task`, `list_agents`, and `search_agents`. This is how the
    orchestrator gets new capabilities over time.

    Args:
        task: A description of what the new specialist should be good at.
        name: Optional explicit name (the LLM picks if omitted).
        category: Optional category hint.
        force: If true, overwrites an existing forged agent with the same name.

    Returns:
        Markdown summary of the new (or reused) specialist, with the first
        chunk of its system prompt.
    """
    orch = _get_orchestrator()
    forged = await orch.forge_agent(task, name=name, category=category, force=force)
    spec = forged.spec
    status = "newly forged" if forged.is_new else "reused existing"
    body_preview = (forged.body or "")[:1500]
    if len(forged.body or "") > 1500:
        body_preview += "\n..."
    return (
        f"# Forged agent: {spec.name} ({status})\n\n"
        f"- category: {spec.category}\n"
        f"- capabilities: {', '.join(spec.capabilities)}\n"
        f"- model: {spec.model}\n"
        f"- description: {spec.description}\n\n"
        f"## System prompt\n\n{body_preview}\n"
    )


@mcp.tool()
def route_only(task: str) -> str:
    """Return the routing decision without executing.

    Cheap dry-run — useful when you want to know which agent the orchestrator
    would pick before committing to running it.

    Args:
        task: The task to classify.

    Returns:
        Markdown describing the routing decision.
    """
    orch = _get_orchestrator()
    decision = orch.router.route(task)
    return (
        f"# Routing decision\n\n"
        f"- primary: **{decision.primary}**\n"
        f"- supporting: {', '.join(decision.supporting) or '(none)'}\n"
        f"- mode: {decision.mode}\n"
        f"- confidence: {decision.confidence:.2f}\n"
        f"- reasoning: {decision.reasoning}\n"
    )


# ---------------------------------------------------------------------------
# Entry point — referenced by pyproject.toml as `forgent-mcp`
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
