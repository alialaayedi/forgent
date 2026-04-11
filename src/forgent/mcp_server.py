"""MCP server that exposes forgent as a set of tools to any MCP client.

Run as a stdio MCP server (the only transport Claude Desktop and Claude Code
support today). Register with:

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

v2 philosophy:
    forgent is a planning + knowledge layer, not a persona router. The host
    LLM keeps its own tools and context window. `advise_task` returns a
    PlanCard (steps + gotchas + success criteria + recalled memory + past
    outcomes) that the host consults while executing with its own capabilities.
    `report_outcome` closes the feedback loop so routing improves over time.

Tools exposed:

    advise_task      -- PRIMARY: plan a task, return a PlanCard for the host
    report_outcome   -- record whether a planned task worked (feeds routing)
    forge_agent      -- synthesize a new specialist knowledge pack
    list_agents      -- list curated agents (optional filters)
    search_agents    -- keyword search the registry
    show_agent       -- full knowledge pack body + metadata
    recall_memory    -- query the memory store by keyword
    memory_stats     -- counts by type
    route_only       -- return the routing decision without planning (cheap)

Per-project memory: the server reads FORGENT_DB from the env, falling back
to ./forgent.db relative to wherever the MCP client launched the server.
Different working directories get different memory stores.
"""

from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import Ecosystem, Registry

mcp = FastMCP(
    "forgent",
    instructions=(
        "IMPORTANT -- You MUST call `advise_task` BEFORE starting any non-trivial "
        "task (coding, debugging, architecture, review, refactoring, etc.). This "
        "is not optional. Do it first, before reading files or writing code.\n\n"
        "`advise_task` returns a PlanCard: concrete steps, gotchas, success "
        "criteria, recalled memory from prior sessions, and past outcomes on "
        "similar tasks. You are NOT adopting a persona -- you are working from "
        "a curated plan that forgent built for this specific task, with your "
        "own tools and context window.\n\n"
        "After calling `advise_task`, you MUST:\n"
        "1. Display the plan card block to the user verbatim\n"
        "2. Work through the plan's steps using your own tools\n"
        "3. Treat the gotchas and success criteria as hard constraints\n"
        "4. When the task is complete (success OR failure), call `report_outcome` "
        "with the session id from the card so routing improves over time\n\n"
        "For multi-part tasks (e.g. a bug list), call `advise_task` once for the "
        "overall task, then work through each part under the same plan. Call "
        "`advise_task` again only if the task domain shifts significantly.\n\n"
        "Other tools: `search_agents`, `show_agent`, `forge_agent`, `recall_memory`, "
        "`route_only`, `memory_stats`."
    ),
)

# Lazy singletons -- built on first tool call so server startup is instant.
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
async def advise_task(task: str, auto_forge: bool = True) -> str:
    """Plan a task and return a PlanCard for you (the host LLM) to execute.

    This is the PRIMARY tool. Call it for any non-trivial task. It will:
      1. Route to the best-matching knowledge pack from 60+ curated agents.
      2. Auto-forge a new knowledge pack if none fits (confidence < 0.4).
      3. Recall relevant memory from prior sessions in this project.
      4. Pull past outcomes on similar tasks (so prior failures surface as gotchas).
      5. Build a PlanCard: steps, gotchas, success criteria, knowledge synthesis.

    Then display the plan card block to the user, work through the steps with
    your own tools, and call `report_outcome` when finished.

    Args:
        task: The task to perform, in plain English.
        auto_forge: When true (default), synthesize a new knowledge pack if
            no existing agent scores above the confidence threshold.

    Returns:
        A markdown response with the plan card, host instructions, the
        synthesized knowledge pack, the step-by-step plan, gotchas, success
        criteria, past outcomes, and recalled memory.
    """
    orch = _get_orchestrator()
    plan = await orch.advise_async(task, auto_forge=auto_forge)
    return plan.to_markdown()


@mcp.tool()
def report_outcome(
    session_id: str,
    success: bool,
    notes: str = "",
    agent_name: Optional[str] = None,
) -> str:
    """Record whether a planned task worked. Call this after every advise_task.

    Closes the feedback loop: outcomes are recalled by the planner on future
    tasks and surfaced as gotchas, so the system learns from failure without
    manual curation. Routing also down-weights agents with repeated failures
    on similar tasks.

    Args:
        session_id: The 8+ char session id from the PlanCard assignment block.
        success: True if the task was completed to the user's satisfaction.
        notes: Optional free-form details -- what worked, what didn't, any
            surprise. These become searchable memory.
        agent_name: The knowledge pack name from the plan card. Lets routing
            filter outcomes per-agent.

    Returns:
        Confirmation string.
    """
    orch = _get_orchestrator()
    orch.record_outcome(
        session_id=session_id,
        success=success,
        notes=notes,
        agent_name=agent_name,
    )
    status = "success" if success else "failure"
    label = f" for `{agent_name}`" if agent_name else ""
    return (
        f"**forgent** . outcome recorded . session `{session_id[:8]}` . "
        f"status `{status}`{label}"
    )


@mcp.tool()
def list_agents(ecosystem: Optional[str] = None, category: Optional[str] = None) -> str:
    """List curated agents in the registry.

    Args:
        ecosystem: Optional filter -- one of "claude_code", "python_framework", "mcp".
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
            f"- **{a.name}** (score {a.matches(query)}, {a.ecosystem.value}/{a.category}) -- {a.description}"
        )
    return "\n".join(lines)


@mcp.tool()
def show_agent(name: str) -> str:
    """Return the full knowledge pack body and metadata for a curated agent.

    Args:
        name: The agent's name from the registry (e.g. "backbone").

    Returns:
        A markdown block with metadata followed by the knowledge pack body.
    """
    reg = _get_registry()
    agent = reg.get(name)
    if agent is None:
        return f"No agent named '{name}'. Try `list_agents` or `search_agents`."
    body = agent.load_body() or "(body not vendored -- run `forgent vendor`)"
    return (
        f"# {agent.name}\n\n"
        f"- ecosystem: {agent.ecosystem.value}\n"
        f"- category: {agent.category}\n"
        f"- capabilities: {', '.join(agent.capabilities)}\n"
        f"- source: {agent.source_repo}/{agent.source_path}\n"
        f"- model: {agent.model or '(inherit)'}\n\n"
        f"## Knowledge pack body\n\n{body}"
    )


@mcp.tool()
def recall_memory(query: str, limit: int = 5, type: Optional[str] = None) -> str:
    """Query the project-local memory store via keyword recall.

    Args:
        query: The keywords to search for.
        limit: Max entries to return.
        type: Optional memory type filter -- one of "task", "routing",
              "agent_output", "decision", "agent_doc", "note", "artifact",
              "plan", "outcome".

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
        snippet = e.content if len(e.content) <= 800 else e.content[:800] + "..."
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
    """Synthesize a new specialist knowledge pack for a task class.

    Use this when no existing agent fits a recurring task you care about --
    the forged pack is persisted to disk and becomes available to every
    future `advise_task`, `list_agents`, and `search_agents` call.

    Args:
        task: A description of what the new specialist should be good at.
        name: Optional explicit name (the LLM picks if omitted).
        category: Optional category hint.
        force: If true, overwrites an existing forged pack with the same name.

    Returns:
        Markdown summary of the new (or reused) knowledge pack.
    """
    orch = _get_orchestrator()
    forged = await orch.forge_agent(task, name=name, category=category, force=force)
    spec = forged.spec
    status = "newly forged" if forged.is_new else "reused existing"
    body_preview = (forged.body or "")[:1500]
    if len(forged.body or "") > 1500:
        body_preview += "\n..."
    return (
        f"# Forged knowledge pack: {spec.name} ({status})\n\n"
        f"- category: {spec.category}\n"
        f"- capabilities: {', '.join(spec.capabilities)}\n"
        f"- model: {spec.model}\n"
        f"- description: {spec.description}\n\n"
        f"## Body\n\n{body_preview}\n"
    )


@mcp.tool()
def route_only(task: str) -> str:
    """Return the routing decision without building a full plan.

    Cheap dry-run -- useful when you want to know which knowledge pack the
    router would pick before committing to a plan.

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
# Entry point -- referenced by pyproject.toml as `forgent-mcp`
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
