"""Plug a new ecosystem into forgent by writing a custom adapter.

The Adapter ABC has one method: `async def run(agent, task, context)`. Any
ecosystem that can take a task and produce a result can be wired in. This
example shows the smallest possible adapter — an echo adapter that doesn't
call any LLM, just for illustration.

In production you'd implement adapters for AutoGen, Semantic Kernel, AWS
Bedrock Agents, Vercel AI SDK, etc. The pattern is identical.

Requires:
    pip install forgent

Run:
    python examples/04_custom_adapter.py
"""

import asyncio

from forgent import Orchestrator
from forgent.adapters.base import Adapter, AdapterResult
from forgent.registry.loader import AgentSpec, Ecosystem


class EchoAdapter(Adapter):
    """Trivial adapter that echoes the task back. Replace with real ecosystem
    integration in production. Example real adapters: AutoGen GroupChat,
    Bedrock Agent invoke, Semantic Kernel Plan execution."""

    ecosystem = Ecosystem.CLAUDE_CODE  # reuse a slot for the demo

    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        return AdapterResult(
            agent=agent.name,
            ecosystem=self.ecosystem,
            output=(
                f"[echo from {agent.name}]\n"
                f"Task: {task}\n"
                f"Context length: {len(context)} chars"
            ),
            success=True,
        )


async def main() -> None:
    orch = Orchestrator()

    # Swap in our custom adapter for the Claude Code ecosystem
    orch.adapters[Ecosystem.CLAUDE_CODE] = EchoAdapter()

    result = await orch.run_async("review my Python code for security issues")
    print(f"Routed to: {result.decision.primary}")
    print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
