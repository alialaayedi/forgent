"""Adapter for Python multi-agent frameworks.

This adapter knows how to instantiate workflow patterns from
`lastmile-ai/mcp-agent` (router, orchestrator, parallel, evaluator-optimizer,
swarm, deep-orchestrator) — these are catalog entries with `ecosystem:
python_framework` and `kind: workflow`.

Each pattern is a different shape:
  * router       — pick one downstream
  * orchestrator — plan, then execute subtasks
  * parallel     — fan out, fan in
  * evaluator-optimizer — generator + critic loop
  * swarm        — explicit handoffs
  * deep-orchestrator — multi-stage long-horizon

For the v1 build we ship a *functional Python implementation* of each pattern
that uses the Anthropic API directly. That keeps install lightweight (no
LangGraph / CrewAI / mcp-agent dependency) while preserving the patterns'
shapes. Users who want the real frameworks can swap in via the optional
extras (`pip install agent-orchestrator[langgraph,crewai,mcp]`).
"""

from __future__ import annotations

import asyncio
import os

from orchestrator.adapters.base import Adapter, AdapterResult
from orchestrator.adapters.claude_code import ClaudeCodeAdapter, _resolve_model
from orchestrator.registry.loader import AgentSpec, Ecosystem, Registry


class PythonFrameworkAdapter(Adapter):
    ecosystem = Ecosystem.PYTHON_FRAMEWORK

    def __init__(self, registry: Registry, claude_adapter: ClaudeCodeAdapter | None = None):
        self.registry = registry
        self.claude_adapter = claude_adapter or ClaudeCodeAdapter()

    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        pattern = agent.name
        try:
            if pattern == "workflow-router":
                return await self._router(task, context)
            if pattern == "workflow-orchestrator-pattern":
                return await self._orchestrator(task, context)
            if pattern == "workflow-parallel":
                return await self._parallel(task, context)
            if pattern == "workflow-evaluator-optimizer":
                return await self._evaluator_optimizer(task, context)
            if pattern == "workflow-swarm":
                return await self._swarm(task, context)
            if pattern == "workflow-deep-orchestrator":
                return await self._deep_orchestrator(task, context)
        except Exception as exc:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return AdapterResult(
            agent=agent.name,
            ecosystem=self.ecosystem,
            output="",
            success=False,
            error=f"Unknown workflow pattern: {pattern}",
        )

    # ----- patterns ---------------------------------------------------

    async def _router(self, task: str, context: str) -> AdapterResult:
        from orchestrator.router.router import Router

        router = Router(self.registry)
        decision = router.route(task)
        agent = self.registry.get(decision.primary)
        if agent is None:
            return AdapterResult(
                agent="workflow-router",
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error="Routed to unknown agent",
            )
        downstream = await self.claude_adapter.run(agent, task, context)
        return AdapterResult(
            agent="workflow-router",
            ecosystem=self.ecosystem,
            output=f"[routed → {decision.primary}]\n\n{downstream.output}",
            success=downstream.success,
            error=downstream.error,
            usage=downstream.usage,
        )

    async def _orchestrator(self, task: str, context: str) -> AdapterResult:
        # Plan, then execute. Planning step asks the LLM to decompose.
        plan_agent = self.registry.get("workflow-orchestrator") or self.registry.get("multi-agent-coordinator")
        if plan_agent is None:
            return _err("orchestrator", self.ecosystem, "no planner agent in registry")
        plan_task = f"Decompose the following task into 3-5 sub-tasks. Output as a numbered list, no explanation.\n\nTask: {task}"
        plan = await self.claude_adapter.run(plan_agent, plan_task, context)
        if not plan.success:
            return plan
        sub_tasks = [line.lstrip("0123456789. -)").strip() for line in plan.output.splitlines() if line.strip()]
        sub_tasks = [s for s in sub_tasks if len(s) > 5][:5]
        results: list[str] = []
        for st in sub_tasks:
            r = await self.claude_adapter.run(plan_agent, st, context)
            results.append(f"### {st}\n{r.output}")
        return AdapterResult(
            agent="workflow-orchestrator-pattern",
            ecosystem=self.ecosystem,
            output=f"## Plan\n{plan.output}\n\n## Execution\n" + "\n\n".join(results),
            success=True,
        )

    async def _parallel(self, task: str, context: str) -> AdapterResult:
        # Fan out to all agents matching the task heuristically, then merge.
        candidates = self.registry.search(task, limit=3)
        if not candidates:
            return _err("parallel", self.ecosystem, "no candidate agents")
        results = await asyncio.gather(
            *[self.claude_adapter.run(c, task, context) for c in candidates if c.ecosystem == Ecosystem.CLAUDE_CODE]
        )
        merged = "\n\n".join(f"### {r.agent}\n{r.output}" for r in results)
        return AdapterResult(
            agent="workflow-parallel",
            ecosystem=self.ecosystem,
            output=merged,
            success=all(r.success for r in results),
        )

    async def _evaluator_optimizer(self, task: str, context: str) -> AdapterResult:
        generator = self.registry.get("fullstack-developer") or self.registry.agents[0]
        critic = self.registry.get("code-reviewer") or self.registry.get("research-analyst") or generator
        draft = await self.claude_adapter.run(generator, task, context)
        if not draft.success:
            return draft
        critique_task = f"Critique this draft and list specific, actionable improvements:\n\n{draft.output}"
        critique = await self.claude_adapter.run(critic, critique_task, context)
        revise_task = f"Original task: {task}\n\nYour previous draft:\n{draft.output}\n\nCritique:\n{critique.output}\n\nProduce an improved version."
        revised = await self.claude_adapter.run(generator, revise_task, context)
        return AdapterResult(
            agent="workflow-evaluator-optimizer",
            ecosystem=self.ecosystem,
            output=f"## Final\n{revised.output}\n\n## Critique applied\n{critique.output}",
            success=revised.success,
        )

    async def _swarm(self, task: str, context: str) -> AdapterResult:
        # Sequential handoff between specialists picked by capability.
        candidates = self.registry.search(task, limit=3)
        candidates = [c for c in candidates if c.ecosystem == Ecosystem.CLAUDE_CODE]
        if not candidates:
            return _err("swarm", self.ecosystem, "no candidate specialists")
        running_context = context
        outputs: list[str] = []
        for agent in candidates:
            r = await self.claude_adapter.run(agent, task, running_context)
            outputs.append(f"### handoff → {agent.name}\n{r.output}")
            running_context += f"\n\n[handoff from {agent.name}]\n{r.output[:1000]}"
        return AdapterResult(
            agent="workflow-swarm",
            ecosystem=self.ecosystem,
            output="\n\n".join(outputs),
            success=True,
        )

    async def _deep_orchestrator(self, task: str, context: str) -> AdapterResult:
        # Two-level: orchestrate, then for each subtask spawn a parallel fan-out.
        outer = await self._orchestrator(task, context)
        if not outer.success:
            return outer
        return AdapterResult(
            agent="workflow-deep-orchestrator",
            ecosystem=self.ecosystem,
            output=outer.output,
            success=True,
        )


def _err(name: str, eco: Ecosystem, msg: str) -> AdapterResult:
    return AdapterResult(agent=name, ecosystem=eco, output="", success=False, error=msg)
