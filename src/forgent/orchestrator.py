"""Top-level Orchestrator — the public API for forgent v2.

In v1 this class dispatched tasks to per-ecosystem adapters that ran their
own tool-use loops. That was a duplication of the host LLM's capabilities
dressed up as a persona swap. v2 removes that layer entirely. The host
(Claude Code, Claude Desktop, any MCP client) keeps its own tools and
context window; forgent contributes planning, memory, and curated domain
knowledge.

Public surface:
    orch.advise(task)           -> PlanCard   # the primary entry point
    orch.forge_agent(task, ...) -> ForgedAgent
    orch.record_outcome(...)    -> None       # feedback loop for routing
    orch.memory, orch.registry, orch.router, orch.planner, orch.forge
"""

from __future__ import annotations

import asyncio
from typing import Any

from forgent.memory import MemoryStore, MemoryType
from forgent.planner import MemoryPath, PlanCard, Planner
from forgent.registry.forge import AgentForge, ForgedAgent
from forgent.registry.loader import Registry
from forgent.router.router import Router

# When the router's confidence is below this threshold and auto_forge is on,
# the orchestrator synthesizes a fresh specialist for the task.
FORGE_CONFIDENCE_THRESHOLD = 0.4


class Orchestrator:
    def __init__(
        self,
        registry: Registry | None = None,
        memory: MemoryStore | None = None,
        db_path: str = "./forgent.db",
    ):
        self.registry = registry or Registry.load()
        self.memory = memory or MemoryStore(db_path)
        self.router = Router(self.registry, memory=self.memory)
        self.planner = Planner(self.registry)
        self.forge = AgentForge(self.registry)

    # ------------------------------------------------------------------
    # The primary entry point: plan, don't execute.
    # ------------------------------------------------------------------

    async def advise_async(
        self,
        task: str,
        metadata: dict[str, Any] | None = None,
        auto_forge: bool = True,
    ) -> PlanCard:
        """Route, recall, plan. Returns a PlanCard the host LLM will execute."""
        sid = self.memory.start_session(task, metadata=metadata)

        # 1. Recall context from prior sessions.
        recalled = self.memory.context_for(task)
        if recalled:
            self.memory.remember(
                f"Recalled context for planned task: {len(recalled)} chars",
                MemoryType.NOTE,
                session_id=sid,
                tags=["recall"],
            )

        # 2. Route via the existing router.
        decision = self.router.route(task)
        forged = False

        # 3. Auto-forge if routing confidence is low.
        if auto_forge and decision.confidence < FORGE_CONFIDENCE_THRESHOLD:
            try:
                forged_agent = await self.forge.forge(task)
                decision.primary = forged_agent.spec.name
                decision.reasoning += f" [auto-forged: {forged_agent.spec.name}]"
                decision.confidence = max(decision.confidence, 0.6)
                forged = True
                self.memory.remember(
                    f"Auto-forged specialist '{forged_agent.spec.name}' "
                    f"(router confidence was {decision.confidence:.2f})",
                    MemoryType.NOTE,
                    session_id=sid,
                    tags=["forge", forged_agent.spec.name],
                )
            except Exception:
                pass  # fall through with original routing

        # 4. Resolve the knowledge pack (the agent's curated .md).
        agent = self.registry.get(decision.primary)
        if agent is None:
            # Shouldn't happen -- router only returns catalog names -- but
            # degrade gracefully by returning a barebones card pointing at
            # whatever the registry's first agent is.
            agent = self.registry.agents[0] if self.registry.agents else None
        if agent is None:
            raise RuntimeError("Registry is empty -- cannot build a plan card")

        # 5. Pull past outcomes for this agent so prior failures bleed into gotchas.
        past_outcomes = self.memory.recent_outcomes(agent_name=agent.name, limit=6)

        # 6. Build the memory index -- a handful of virtual paths pointing at
        #    relevant prior context. This is the primary memory surface in
        #    v0.3: the host pulls detail via memory_view on demand instead of
        #    parsing a dumped recall string.
        memory_index = self._build_memory_index(agent_name=agent.name)

        # 7. Build the PlanCard.
        plan = self.planner.plan(
            task=task,
            session_id=sid,
            decision=decision,
            agent=agent,
            recalled_memory=recalled,
            past_outcomes=past_outcomes,
            memory_index=memory_index,
            forged=forged,
        )

        # 8. Persist the routing decision and the plan itself.
        self.memory.remember(
            f"Routed to '{decision.primary}' (mode={decision.mode}, "
            f"supporting={decision.supporting}). Reason: {decision.reasoning}",
            MemoryType.ROUTING,
            session_id=sid,
            tags=[decision.primary, decision.mode, "advise"],
        )
        plan_summary = (
            f"Plan for '{task[:120]}' via {decision.primary}. "
            f"Steps: {' | '.join(plan.steps[:3])}"
        )
        self.memory.remember(
            plan_summary,
            MemoryType.PLAN,
            session_id=sid,
            source=decision.primary,
            tags=[decision.primary, "plan"],
        )
        self.memory.close_session(sid, status="advised")
        return plan

    def advise(
        self,
        task: str,
        metadata: dict[str, Any] | None = None,
        auto_forge: bool = True,
    ) -> PlanCard:
        return asyncio.run(self.advise_async(task, metadata, auto_forge=auto_forge))

    # ------------------------------------------------------------------
    # Outcome reporting -- closes the feedback loop.
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        session_id: str,
        success: bool,
        notes: str = "",
        agent_name: str | None = None,
    ) -> None:
        """Record whether a planned task succeeded.

        Outcomes are retrieved by the planner and surfaced as gotchas on
        future plans for the same agent, so the system learns from failure
        without manual curation.
        """
        self.memory.record_outcome(
            session_id=session_id,
            success=success,
            notes=notes,
            agent_name=agent_name,
        )

    # ------------------------------------------------------------------
    # Memory index -- the progressive-recall surface for v0.3 PlanCards.
    # ------------------------------------------------------------------

    def _build_memory_index(self, agent_name: str) -> list[MemoryPath]:
        """Return a compact set of virtual paths pointing at relevant memory.

        We don't try to be clever here -- the planner LLM sees these paths
        alongside the task and can weigh relevance downstream. The shape
        mirrors Anthropic's memory tool tree so the host can browse them via
        memory_view on demand.

        Included by default:
          - /outcomes/<agent>/ if this agent has any prior outcomes
          - /plans/<agent>/    if this agent has any prior plans
          - up to 5 /notes/<topic>/ directories (host-written breadcrumbs)
          - /sessions/ as a catch-all for "what did I do recently"
        """
        index: list[MemoryPath] = []

        def _surface(kind: str) -> None:
            """Prefer the current agent's scoped dir; fall back to the top-level dir."""
            children = self.memory.list_paths(f"/{kind}/")
            if not children:
                return
            agent_hit = next(
                (e for e in children if e["path"].endswith(f"/{agent_name}/")),
                None,
            )
            if agent_hit is not None:
                index.append(
                    MemoryPath(
                        path=agent_hit["path"],
                        label=agent_hit["label"],
                        count=agent_hit["count"],
                    )
                )
                return
            total = sum(e.get("count", 0) for e in children)
            index.append(
                MemoryPath(
                    path=f"/{kind}/",
                    label=f"{total} {kind} entries across {len(children)} agent(s)",
                    count=total,
                )
            )

        _surface("outcomes")
        _surface("plans")

        notes_root = self.memory.list_paths("/notes/")
        for entry in notes_root[:5]:
            index.append(
                MemoryPath(
                    path=entry["path"],
                    label=entry["label"],
                    count=entry["count"],
                )
            )

        sessions_root = self.memory.list_paths("/")
        for entry in sessions_root:
            if entry["path"] == "/sessions/":
                index.append(
                    MemoryPath(
                        path="/sessions/",
                        label=f"{entry['count']} prior sessions -- browse the most recent",
                        count=entry["count"],
                    )
                )
                break

        return index

    # ------------------------------------------------------------------

    async def forge_agent(
        self,
        task: str,
        name: str | None = None,
        category: str | None = None,
        force: bool = False,
    ) -> ForgedAgent:
        """Synthesize a new specialist subagent and add it to the registry."""
        return await self.forge.forge(task, name=name, category=category, force=force)
