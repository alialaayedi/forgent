"""End-to-end smoke test that doesn't require an API key.

Runs the orchestrator with the LLM router disabled (no key, falls back to
heuristic) and a fake adapter so the full flow exercises:
    registry load → context recall → router → adapter dispatch → memory write
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from forgent.adapters.base import Adapter, AdapterResult
from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.registry.loader import AgentSpec, Ecosystem, Registry


class FakeClaudeAdapter(Adapter):
    ecosystem = Ecosystem.CLAUDE_CODE

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        self.calls.append((agent.name, task))
        return AdapterResult(
            agent=agent.name,
            ecosystem=self.ecosystem,
            output=f"[fake output from {agent.name}] {task[:50]}",
            success=True,
        )


def _fresh_orchestrator(tmp_path: Path) -> tuple[Orchestrator, FakeClaudeAdapter]:
    os.environ.pop("ANTHROPIC_API_KEY", None)  # force heuristic router
    db = tmp_path / "test.db"
    reg = Registry.load()
    mem = MemoryStore(db)
    orch = Orchestrator(registry=reg, memory=mem, db_path=str(db))
    fake = FakeClaudeAdapter()
    orch.adapters[Ecosystem.CLAUDE_CODE] = fake
    # PythonFrameworkAdapter delegates to claude_adapter; swap that too
    orch.claude_adapter = fake  # type: ignore
    orch.adapters[Ecosystem.PYTHON_FRAMEWORK].claude_adapter = fake  # type: ignore
    return orch, fake


def test_registry_loads_with_50_plus_agents():
    reg = Registry.load()
    assert len(reg) >= 50, f"expected 50+ curated agents, got {len(reg)}"
    ecosystems = {a.ecosystem for a in reg}
    assert Ecosystem.CLAUDE_CODE in ecosystems
    assert Ecosystem.PYTHON_FRAMEWORK in ecosystems
    assert Ecosystem.MCP in ecosystems


def test_registry_search_finds_relevant_agents():
    reg = Registry.load()
    matches = reg.search("kubernetes security")
    names = [m.name for m in matches]
    assert "kubernetes-specialist" in names or "security-auditor" in names


def test_memory_store_recalls_keyword_matches(tmp_path):
    mem = MemoryStore(tmp_path / "mem.db")
    sid = mem.start_session("design a payment webhook")
    mem.remember("Used payment-integration agent for Stripe webhook", MemoryType.ROUTING, session_id=sid)
    mem.remember("Implemented HMAC signature verification", MemoryType.AGENT_OUTPUT, session_id=sid)
    mem.remember("Unrelated note about deploying a static site", MemoryType.NOTE, session_id=sid)

    routing_recall = mem.recall("Stripe webhook", limit=3, type=MemoryType.ROUTING)
    assert any("payment-integration" in e.content for e in routing_recall)

    output_recall = mem.recall("HMAC", limit=3, type=MemoryType.AGENT_OUTPUT)
    assert any("HMAC" in e.content for e in output_recall)


def test_orchestrator_end_to_end_with_fake_adapter(tmp_path):
    orch, fake = _fresh_orchestrator(tmp_path)
    result = asyncio.run(orch.run_async("review my Python code for security issues"))
    assert result.success
    assert len(fake.calls) >= 1
    # Heuristic should have picked something security or python related
    picked = result.decision.primary
    assert picked  # not empty
    # Memory should now have a routing entry and an agent_output entry
    stats = orch.memory.stats()
    assert stats.get("routing", 0) >= 1
    assert stats.get("agent_output", 0) >= 1


def test_orchestrator_recalls_context_on_second_run(tmp_path):
    orch, fake = _fresh_orchestrator(tmp_path)
    asyncio.run(orch.run_async("write an OpenAPI spec for a user service"))
    # Second run on a related task should pull context from the first
    context = orch.memory.context_for("add a new endpoint to the user service")
    assert "user service" in context.lower() or "openapi" in context.lower()


def test_forge_stub_creates_agent_without_api_key(tmp_path):
    # No API key → stub mode
    orch, fake = _fresh_orchestrator(tmp_path)
    forged = asyncio.run(orch.forge_agent("write Solidity contracts with formal verification"))
    assert forged.is_new
    assert forged.spec.name in [a.name for a in orch.registry.agents]
    assert "specialist" in forged.spec.name or "expert" in forged.spec.name or len(forged.spec.name) > 0
    assert forged.body  # body must not be empty
    # The new agent should now be findable via the registry
    found = orch.registry.get(forged.spec.name)
    assert found is not None
    assert found.system_prompt == forged.body


def test_forge_with_auto_forge_routes_to_new_agent(tmp_path, monkeypatch):
    # Force auto-forge by raising the confidence threshold above what the
    # heuristic router can produce.
    monkeypatch.setattr("forgent.orchestrator.FORGE_CONFIDENCE_THRESHOLD", 0.99)
    orch, fake = _fresh_orchestrator(tmp_path)
    result = asyncio.run(orch.run_async(
        "build a thing that does the stuff",
        auto_forge=True,
    ))
    forged_names = [a.name for a in orch.registry.agents if a.source_repo in ("forge", "forge-stub")]
    assert len(forged_names) > 0
    assert result.decision.primary in [a.name for a in orch.registry.agents]


def test_mcp_server_module_imports():
    # Smoke check that the FastMCP server module loads and tools are registered
    from forgent.mcp_server import mcp
    assert mcp.name == "forgent"


def test_progress_emitter_receives_all_steps(tmp_path):
    """Verify the orchestrator calls every progress checkpoint at least once."""
    from forgent.progress import Progress

    received: list[tuple[str, tuple, dict]] = []

    class RecordingProgress:
        def __getattr__(self, name):
            def _capture(*args, **kwargs):
                received.append((name, args, kwargs))
            return _capture

        def to_markdown(self):
            return ""

    orch, fake = _fresh_orchestrator(tmp_path)
    asyncio.run(orch.run_async("review my Python code", progress=RecordingProgress()))

    method_names = [name for name, _args, _kwargs in received]
    # Every checkpoint must have fired
    assert "start" in method_names
    assert "recall" in method_names
    assert "route" in method_names
    assert "dispatch" in method_names
    assert "dispatch_done" in method_names
    assert "persist" in method_names
    assert "done" in method_names


def test_mcp_context_progress_builds_markdown_trace(tmp_path):
    """The MCP-side emitter accumulates a markdown trace even with no ctx."""
    from forgent.progress import MCPContextProgress

    progress = MCPContextProgress(ctx=None)  # no MCP client — just trace mode

    orch, fake = _fresh_orchestrator(tmp_path)
    asyncio.run(orch.run_async("design a webhook handler", progress=progress))

    trace = progress.to_markdown()
    assert "## trace" in trace
    assert "task" in trace
    assert "route" in trace
    assert "dispatch" in trace
    assert "persist" in trace

    # Structured items should also be available for compact rendering
    items = progress.trace_items()
    assert len(items) >= 5  # task + recall + route + dispatch + persist + done
    labels = [label for label, _body in items]
    assert "task" in labels
    assert "route" in labels
    assert "persist" in labels


def test_mcp_run_task_formatter_success(tmp_path):
    """The new run_task formatter renders a success run as scannable markdown."""
    from forgent.mcp_server import _format_run_response
    from forgent.progress import MCPContextProgress

    progress = MCPContextProgress(ctx=None)
    orch, fake = _fresh_orchestrator(tmp_path)
    # Task is chosen so the heuristic router lands on a claude_code agent
    # (python-pro / security-auditor / backend-developer) — all routed
    # through the fake adapter installed by _fresh_orchestrator. Avoids
    # words like "write" / "fetch" / "files" that would match MCP agents.
    result = asyncio.run(
        orch.run_async("review this Python class for security bugs", progress=progress)
    )

    formatted = _format_run_response("review this Python class for security bugs", result, progress)

    # Hero line
    assert "**forgent**" in formatted
    assert "session" in formatted
    # Compact trace
    assert "**trace**" in formatted
    assert "`route`" in formatted
    # Routing block
    assert "**routing**" in formatted
    assert "primary:" in formatted
    # Preview blockquote
    assert "**preview**" in formatted
    assert formatted.count("> ") >= 1
    # Collapsed full output
    assert "<details>" in formatted
    assert "<summary>" in formatted
    assert "full output" in formatted
    # Next-steps menu
    assert "**next**" in formatted
    assert "recall_memory" in formatted
    assert "route_only" in formatted
    assert "forge_agent" in formatted


def test_mcp_run_task_formatter_failure_includes_remediation(tmp_path):
    """When run_task fails, the formatter includes how-to-fix guidance."""
    import os
    from forgent.adapters.base import Adapter, AdapterResult
    from forgent.mcp_server import _format_run_response
    from forgent.orchestrator import Orchestrator
    from forgent.progress import MCPContextProgress
    from forgent.registry.loader import AgentSpec, Ecosystem, Registry

    class FailingAdapter(Adapter):
        ecosystem = Ecosystem.CLAUDE_CODE

        async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error="ANTHROPIC_API_KEY not set or anthropic SDK not installed",
            )

    os.environ.pop("ANTHROPIC_API_KEY", None)
    orch = Orchestrator(registry=Registry.load(), db_path=str(tmp_path / "fail.db"))
    orch.adapters[Ecosystem.CLAUDE_CODE] = FailingAdapter()
    orch.claude_adapter = FailingAdapter()
    orch.adapters[Ecosystem.PYTHON_FRAMEWORK].claude_adapter = FailingAdapter()  # type: ignore

    progress = MCPContextProgress(ctx=None)
    result = asyncio.run(orch.run_async("any failing task", progress=progress))

    formatted = _format_run_response("any failing task", result, progress)

    assert "failed" in formatted
    assert "**errors**" in formatted
    assert "ANTHROPIC_API_KEY" in formatted
    assert "**how to fix" in formatted
    assert "claude mcp add" in formatted
