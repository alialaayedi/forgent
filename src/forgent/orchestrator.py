"""Top-level Orchestrator — the public API.

Pulls together the registry, the router, the memory store, and all adapters.
A single `Orchestrator.run(task)` call:
    1. opens a session in memory
    2. recalls relevant past context
    3. routes the task to one or more agents
    4. dispatches via the matching adapter(s)
    5. persists every step back into memory
    6. returns the merged output

Use it as a library, or via the CLI in `cli.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from forgent.adapters import (
    Adapter,
    AdapterResult,
    ClaudeCodeAdapter,
    MCPAdapter,
    PythonFrameworkAdapter,
)
from forgent.memory import MemoryStore, MemoryType
from forgent.progress import NullProgress, Progress
from forgent.registry.forge import AgentForge, ForgedAgent
from forgent.registry.loader import Ecosystem, Registry
from forgent.router.router import Router, RoutingDecision

# When the router's confidence is below this threshold and auto_forge is on,
# the orchestrator synthesizes a fresh specialist for the task.
FORGE_CONFIDENCE_THRESHOLD = 0.4


@dataclass
class RunResult:
    task: str
    session_id: str
    decision: RoutingDecision
    results: list[AdapterResult] = field(default_factory=list)

    @property
    def output(self) -> str:
        if not self.results:
            return ""
        if len(self.results) == 1:
            return self.results[0].output
        return "\n\n".join(f"## {r.agent}\n{r.output}" for r in self.results)

    @property
    def success(self) -> bool:
        return bool(self.results) and all(r.success for r in self.results)


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
        self.forge = AgentForge(self.registry)
        self.claude_adapter = ClaudeCodeAdapter()
        self.adapters: dict[Ecosystem, Adapter] = {
            Ecosystem.CLAUDE_CODE: self.claude_adapter,
            Ecosystem.PYTHON_FRAMEWORK: PythonFrameworkAdapter(self.registry, self.claude_adapter),
            Ecosystem.MCP: MCPAdapter(),
        }

    # ------------------------------------------------------------------

    async def run_async(
        self,
        task: str,
        metadata: dict[str, Any] | None = None,
        auto_forge: bool = False,
        progress: Progress | None = None,
    ) -> RunResult:
        prog: Progress = progress or NullProgress()
        prog.start(task)

        sid = self.memory.start_session(task, metadata=metadata)

        # 1. Recall — assemble context from past sessions
        context = self.memory.context_for(task)
        prog.recall(len(context))
        if context:
            self.memory.remember(
                f"Recalled context for task: {len(context)} chars",
                MemoryType.NOTE,
                session_id=sid,
                tags=["recall"],
            )

        # 2. Route
        decision = self.router.route(task)

        # 2b. If auto_forge is on and the router isn't confident, synthesize a
        #     fresh specialist for this task and re-route to it.
        if auto_forge and decision.confidence < FORGE_CONFIDENCE_THRESHOLD:
            forged = await self.forge.forge(task)
            self.memory.remember(
                f"Auto-forged specialist '{forged.spec.name}' "
                f"(router confidence was {decision.confidence:.2f})",
                MemoryType.NOTE,
                session_id=sid,
                tags=["forge", forged.spec.name],
            )
            decision.primary = forged.spec.name
            decision.reasoning += f" [auto-forged: {forged.spec.name}]"
            decision.confidence = max(decision.confidence, 0.6)

        prog.route(decision.primary, decision.supporting, decision.mode, decision.confidence)

        self.memory.remember(
            f"Routed to '{decision.primary}' (mode={decision.mode}, "
            f"supporting={decision.supporting}). Reason: {decision.reasoning}",
            MemoryType.ROUTING,
            session_id=sid,
            tags=[decision.primary, decision.mode],
        )

        # 3. Execute
        agents_to_run = [self.registry.get(decision.primary)]
        if decision.mode in ("parallel", "evaluator-optimizer"):
            agents_to_run.extend(
                self.registry.get(name) for name in decision.supporting
            )
        agents_to_run = [a for a in agents_to_run if a is not None]

        async def _dispatch_with_progress(agent, ctx_str: str) -> AdapterResult:
            prog.dispatch(agent.name, agent.ecosystem.value)
            r = await self._dispatch(agent, task, ctx_str)
            prog.dispatch_done(agent.name, r.success, len(r.output))
            return r

        results: list[AdapterResult] = []
        if decision.mode == "parallel" and len(agents_to_run) > 1:
            results = await asyncio.gather(
                *[_dispatch_with_progress(a, context) for a in agents_to_run]
            )
        elif decision.mode == "sequential":
            running_context = context
            for a in agents_to_run:
                r = await _dispatch_with_progress(a, running_context)
                results.append(r)
                running_context += f"\n\n[from {a.name}]\n{r.output[:2000]}"
        else:
            # single, evaluator-optimizer (delegated inside the python_framework adapter), or fallback
            r = await _dispatch_with_progress(agents_to_run[0], context)
            results.append(r)

        # 4. Persist outputs
        for r in results:
            self.memory.remember(
                r.output,
                MemoryType.AGENT_OUTPUT,
                session_id=sid,
                source=r.agent,
                tags=[r.agent, r.ecosystem.value, "success" if r.success else "error"],
            )

        prog.persist(sid, len(results))
        success = bool(results) and all(r.success for r in results)
        prog.done(success)

        self.memory.close_session(sid, status="completed" if success else "error")
        return RunResult(task=task, session_id=sid, decision=decision, results=list(results))

    def run(
        self,
        task: str,
        metadata: dict[str, Any] | None = None,
        auto_forge: bool = False,
        progress: Progress | None = None,
    ) -> RunResult:
        return asyncio.run(self.run_async(task, metadata, auto_forge=auto_forge, progress=progress))

    async def forge_agent(
        self,
        task: str,
        name: str | None = None,
        category: str | None = None,
        force: bool = False,
    ) -> ForgedAgent:
        """Synthesize a new specialist subagent and add it to the registry."""
        return await self.forge.forge(task, name=name, category=category, force=force)

    # ------------------------------------------------------------------

    async def _dispatch(self, agent, task: str, context: str) -> AdapterResult:
        adapter = self.adapters.get(agent.ecosystem)
        if adapter is None:
            return AdapterResult(
                agent=agent.name,
                ecosystem=agent.ecosystem,
                output="",
                success=False,
                error=f"No adapter registered for ecosystem {agent.ecosystem}",
            )
        return await adapter.run(agent, task, context)
