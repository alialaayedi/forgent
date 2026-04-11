"""End-to-end smoke tests for forgent v2 (planning layer, no adapters).

Runs against the heuristic planner + router (no API key required). Exercises:
    registry load -> memory recall -> router -> planner -> PlanCard -> outcome
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from forgent.memory import MemoryStore, MemoryType
from forgent.orchestrator import Orchestrator
from forgent.planner import PlanCard
from forgent.registry.loader import Ecosystem, Registry


def _fresh_orchestrator(tmp_path: Path) -> Orchestrator:
    os.environ.pop("ANTHROPIC_API_KEY", None)  # force heuristic planner/router
    db = tmp_path / "test.db"
    reg = Registry.load()
    mem = MemoryStore(db)
    return Orchestrator(registry=reg, memory=mem, db_path=str(db))


# ---------------------------------------------------------------------------
# Registry + memory primitives
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Outcome tracking
# ---------------------------------------------------------------------------


def test_record_outcome_persists_and_is_recallable(tmp_path):
    mem = MemoryStore(tmp_path / "mem.db")
    sid = mem.start_session("ship a feature")
    mem.record_outcome(
        session_id=sid,
        success=False,
        notes="tests failed on migration",
        agent_name="backbone",
    )
    entries = mem.recent_outcomes(agent_name="backbone")
    assert len(entries) == 1
    assert entries[0].type == MemoryType.OUTCOME
    assert "failure" in entries[0].content
    assert "backbone" in entries[0].content


def test_recent_outcomes_filters_by_agent(tmp_path):
    mem = MemoryStore(tmp_path / "mem.db")
    sid = mem.start_session("mixed work")
    mem.record_outcome(session_id=sid, success=True, agent_name="alpha")
    mem.record_outcome(session_id=sid, success=False, agent_name="beta")
    mem.record_outcome(session_id=sid, success=True, agent_name="alpha")

    alpha = mem.recent_outcomes(agent_name="alpha")
    assert len(alpha) == 2
    assert all("alpha" in e.content for e in alpha)

    all_outcomes = mem.recent_outcomes()
    assert len(all_outcomes) == 3


# ---------------------------------------------------------------------------
# Planner + PlanCard (heuristic path, no API key)
# ---------------------------------------------------------------------------


def test_advise_returns_plan_card(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("review my Python code for security issues"))

    assert isinstance(plan, PlanCard)
    assert plan.task
    assert plan.session_id
    assert plan.primary_agent  # router picked something from the catalog
    assert plan.heuristic is True  # no API key -> heuristic planner
    assert plan.steps  # heuristic fills these in
    assert plan.success_criteria
    assert plan.gotchas


def test_plan_card_to_markdown_has_required_sections(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("write an OpenAPI spec for a user service"))
    md = plan.to_markdown()

    # Assignment block must be present and renderable
    assert "forgent -- plan card" in md
    assert "```" in md
    # Host instructions
    assert "DISPLAY THE BLOCK ABOVE TO THE USER" in md
    assert "report_outcome" in md
    # Plan sections
    assert "## Plan" in md
    assert "## Gotchas" in md
    assert "## Success criteria" in md


def test_advise_writes_plan_and_routing_to_memory(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    asyncio.run(orch.advise_async("design a webhook handler"))
    stats = orch.memory.stats()
    assert stats.get("routing", 0) >= 1
    assert stats.get("plan", 0) >= 1


def test_second_advise_pulls_prior_context(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    asyncio.run(orch.advise_async("write an OpenAPI spec for a user service"))
    # Second advise on a related task should recall context from the first
    context = orch.memory.context_for("add a new endpoint to the user service")
    assert "user service" in context.lower() or "openapi" in context.lower()


def test_past_outcomes_surface_in_plan(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    # First run -- gets the agent name the router picks
    plan1 = asyncio.run(orch.advise_async("review my Python code for security bugs"))
    orch.record_outcome(
        session_id=plan1.session_id,
        success=False,
        notes="missed SQL injection in user query builder",
        agent_name=plan1.primary_agent,
    )
    # Second run -- outcomes recall should show the prior failure for that agent
    plan2 = asyncio.run(orch.advise_async("audit Python security across the API layer"))
    # Either directly via past_outcomes if same agent matched, or via recalled memory
    surfaced = (
        any("SQL injection" in o or "failure" in o for o in plan2.past_outcomes)
        or "SQL injection" in plan2.recalled_memory
    )
    assert surfaced


def test_orchestrator_record_outcome_roundtrip(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("add a payment refund endpoint"))
    orch.record_outcome(
        session_id=plan.session_id,
        success=True,
        notes="implemented with idempotency key",
        agent_name=plan.primary_agent,
    )
    outcomes = orch.memory.recent_outcomes(agent_name=plan.primary_agent)
    assert len(outcomes) == 1
    assert "success" in outcomes[0].content
    assert "idempotency" in outcomes[0].content


# ---------------------------------------------------------------------------
# Forge still works
# ---------------------------------------------------------------------------


def test_forge_stub_creates_agent_without_api_key(tmp_path):
    orch = _fresh_orchestrator(tmp_path)
    forged = asyncio.run(orch.forge_agent("write Solidity contracts with formal verification"))
    assert forged.is_new
    assert forged.spec.name in [a.name for a in orch.registry.agents]
    assert forged.body
    found = orch.registry.get(forged.spec.name)
    assert found is not None
    assert found.system_prompt == forged.body


def test_auto_forge_kicks_in_on_low_confidence(tmp_path, monkeypatch):
    # Force auto-forge by raising the confidence threshold above what the
    # heuristic router can produce.
    monkeypatch.setattr("forgent.orchestrator.FORGE_CONFIDENCE_THRESHOLD", 0.99)
    orch = _fresh_orchestrator(tmp_path)
    plan = asyncio.run(orch.advise_async("build a thing that does the stuff", auto_forge=True))
    forged_names = [
        a.name for a in orch.registry.agents if a.source_repo in ("forge", "forge-stub")
    ]
    assert len(forged_names) > 0
    assert plan.primary_agent in [a.name for a in orch.registry.agents]
    assert plan.forged is True


# ---------------------------------------------------------------------------
# MCP server imports cleanly
# ---------------------------------------------------------------------------


def test_mcp_server_module_imports():
    from forgent.mcp_server import mcp
    assert mcp.name == "forgent"


def test_mcp_server_exposes_advise_and_outcome_tools():
    """Spot-check that the key v2 tools are registered."""
    import asyncio as _asyncio

    from forgent.mcp_server import mcp

    tools = _asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "advise_task" in names
    assert "report_outcome" in names
    assert "forge_agent" in names
    assert "recall_memory" in names
    # v1 tools that should be gone
    assert "run_task" not in names
