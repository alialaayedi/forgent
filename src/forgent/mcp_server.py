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

from mcp.server.fastmcp import Context, FastMCP

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.progress import MCPContextProgress
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


# ---------------------------------------------------------------------------
# Response formatter
# ---------------------------------------------------------------------------


# Cap the inline full-output block so the response stays scannable.
# Anything beyond this is reachable via recall_memory.
_MAX_INLINE_OUTPUT = 12_000
_PREVIEW_CHARS = 240


def _format_run_response(task: str, result: Any, progress: "MCPContextProgress") -> str:
    """Render a `run_task` result as a tight, scannable markdown response.

    Layout:
        forgent · <agent> · <duration>
        <one-line context: success or failure summary>

        **trace**
        - recall: ...
        - route: ...
        - dispatch: ...
        - persist: ...
        - done: ...

        **routing**
        - primary, mode, confidence, reasoning

        **preview**
        > first ~240 chars of the output, blockquoted

        <details>
        <summary>full output · N chars</summary>
        ...full agent output...
        </details>

        **next**
        - suggested follow-up tool calls
    """
    d = result.decision
    elapsed = progress.elapsed()
    short_session = result.session_id[:8]

    # ----- hero line ----------------------------------------------------
    if result.success:
        hero = f"**forgent** · **`{d.primary}`** · {elapsed:.1f}s · session `{short_session}`"
    else:
        hero = f"**forgent** · failed · session `{short_session}`"

    # ----- compact trace -----------------------------------------------
    trace_items = progress.trace_items()
    if trace_items:
        trace_block = "**trace**\n" + "\n".join(
            f"- `{label}` — {body}" for label, body in trace_items
        )
    else:
        trace_block = ""

    # ----- routing block ------------------------------------------------
    sup = f" + {', '.join(d.supporting)}" if d.supporting else ""
    routing_block = (
        "**routing**\n"
        f"- primary: `{d.primary}`{sup}\n"
        f"- mode: `{d.mode}` · confidence: `{d.confidence:.2f}`\n"
        f"- reasoning: {d.reasoning}"
    )

    # ----- failure path -------------------------------------------------
    if not result.success:
        errors = "\n".join(
            f"- `{r.agent}` ({r.ecosystem.value}) — {r.error}"
            for r in result.results
            if r.error
        ) or "- (no specific errors reported)"

        return "\n\n".join([
            hero,
            trace_block,
            routing_block,
            "**errors**\n" + errors,
            (
                "**how to fix the most common one**\n"
                "If you see `ANTHROPIC_API_KEY not set`, the forgent MCP server "
                "subprocess doesn't have your key. Either:\n"
                "1. Add the key to your shell rc and re-register forgent: "
                "`claude mcp remove forgent && claude mcp add --scope user forgent "
                "--env ANTHROPIC_API_KEY=\"$ANTHROPIC_API_KEY\" -- /path/to/forgent-mcp`\n"
                "2. Restart the Claude Code window so the subprocess respawns "
                "with the current registration env."
            ),
        ])

    # ----- success path -------------------------------------------------
    raw_output = result.output or ""
    total_chars = len(raw_output)

    # Single-line preview (collapse newlines so it stays one paragraph)
    preview = raw_output[:_PREVIEW_CHARS].replace("\n", " ").strip()
    if total_chars > _PREVIEW_CHARS:
        preview += "…"
    preview_block = f"**preview**\n\n> {preview}" if preview else ""

    # Full output, capped, in a collapsed details block so the response
    # doesn't dominate the conversation
    if total_chars > _MAX_INLINE_OUTPUT:
        body = (
            raw_output[:_MAX_INLINE_OUTPUT]
            + f"\n\n*[truncated · {total_chars - _MAX_INLINE_OUTPUT:,} more chars · "
            f"use `recall_memory query=\"{short_session}\"` to retrieve the full output]*"
        )
    else:
        body = raw_output

    # NOTE: <details> renders as a click-to-expand block in Claude Code,
    # Cursor, GitHub markdown, and most other modern markdown renderers.
    full_output_block = (
        f"<details>\n"
        f"<summary><b>full output</b> · {total_chars:,} chars</summary>\n\n"
        f"{body}\n\n"
        f"</details>"
    )

    # Suggested follow-ups make the response feel actionable
    next_block = (
        "**next**\n"
        f"- `recall_memory query=\"{short_session}\"` — retrieve this session's outputs later\n"
        "- `route_only task=\"…\"` — try a different routing decision without executing\n"
        "- `forge_agent task=\"…\"` — synthesize a permanent specialist for this task class\n"
        "- `search_agents query=\"…\"` — find related curated agents in the registry"
    )

    return "\n\n".join(
        block for block in [
            hero,
            trace_block,
            routing_block,
            preview_block,
            full_output_block,
            next_block,
        ] if block
    )


@mcp.tool()
async def run_task(task: str, auto_forge: bool = False, ctx: Context = None) -> str:
    """Run a task end-to-end through the orchestrator.

    Routes the task to the best curated agent, executes via the matching
    ecosystem adapter, persists everything to the project-local memory
    store, and returns a *scannable* markdown response — hero summary,
    compact trace, routing details, output preview, and a collapsible
    full-output block (so the response never floods the conversation).

    While running, this tool also emits MCP `notifications/progress` and
    `notifications/log` so clients that render them show a live trace
    instead of a generic spinner.

    Args:
        task: The task to perform, in plain English.
        auto_forge: If true, synthesizes a brand-new specialist subagent when
            the router's confidence in the existing catalog is low. The new
            agent is persisted and reused for future tasks of the same shape.
        ctx: FastMCP-injected context (for progress notifications). Don't
            pass this manually — FastMCP fills it in automatically.

    Returns:
        A scannable markdown response with hero, trace, routing, preview,
        collapsed full output, and suggested follow-up tool calls.
    """
    orch = _get_orchestrator()
    progress = MCPContextProgress(ctx=ctx)
    result = await orch.run_async(task, auto_forge=auto_forge, progress=progress)
    return _format_run_response(task, result, progress)


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
