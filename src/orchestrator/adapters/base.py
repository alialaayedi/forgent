"""Common adapter interface.

Every ecosystem adapter implements `Adapter.run`, taking a curated `AgentSpec`,
a task string, and a context block (already assembled by the orchestrator from
memory recall), and returning a structured `AdapterResult`.

Adapters are async so the orchestrator can run several in parallel via
`asyncio.gather` when the routing mode is `parallel`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from orchestrator.registry.loader import AgentSpec, Ecosystem


@dataclass
class AdapterResult:
    agent: str
    ecosystem: Ecosystem
    output: str
    success: bool = True
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "ecosystem": self.ecosystem.value,
            "output": self.output,
            "success": self.success,
            "error": self.error,
            "artifacts": self.artifacts,
            "usage": self.usage,
        }


class Adapter(ABC):
    ecosystem: Ecosystem

    @abstractmethod
    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        """Execute one agent run. Must not raise — wrap errors in AdapterResult."""

    def supports(self, agent: AgentSpec) -> bool:
        return agent.ecosystem == self.ecosystem
