"""LLM-based task router.

The router takes a free-form task and returns a `RoutingDecision`:
    * primary agent  — who runs the task
    * supporting agents — who can be consulted in parallel
    * mode  — single | sequential | parallel | evaluator-optimizer
    * reasoning — why this routing was chosen (stored in memory)

It uses the Anthropic API with structured tool-use to force a clean JSON
response. If `ANTHROPIC_API_KEY` is missing, it gracefully falls back to a
keyword-scoring heuristic so the orchestrator still works offline.

Past routing decisions from the memory store are pulled in as few-shot
context, so the router learns from prior choices over time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forgent.memory.store import MemoryStore
    from forgent.registry.loader import Registry, AgentSpec

ROUTER_MODEL_DEFAULT = "claude-haiku-4-5-20251001"


@dataclass
class RoutingDecision:
    primary: str                                  # agent name
    supporting: list[str] = field(default_factory=list)
    mode: str = "single"                          # single | sequential | parallel | evaluator-optimizer
    reasoning: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "primary": self.primary,
            "supporting": self.supporting,
            "mode": self.mode,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }


class Router:
    def __init__(
        self,
        registry: "Registry",
        memory: "MemoryStore | None" = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.registry = registry
        self.memory = memory
        self.model = model or os.environ.get("FORGENT_ROUTER_MODEL", ROUTER_MODEL_DEFAULT)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None
        if self.api_key:
            try:
                import anthropic  # noqa: WPS433
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self._client = None

    # ------------------------------------------------------------------

    def route(self, task: str) -> RoutingDecision:
        """Pick the best agent(s) for this task.

        Tries the LLM router first, falls back to keyword scoring on any error
        (no API key, network blip, malformed response). Both paths return a
        valid RoutingDecision so callers never need to handle None.
        """
        if self._client is not None:
            try:
                return self._llm_route(task)
            except Exception as exc:
                # Fall through to heuristic — but record why
                if self.memory is not None:
                    from forgent.memory.store import MemoryType
                    self.memory.remember(
                        f"Router LLM failed, falling back to heuristic: {exc}",
                        MemoryType.NOTE,
                        tags=["router", "fallback"],
                    )
        return self._heuristic_route(task)

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _llm_route(self, task: str) -> RoutingDecision:
        catalog = self._compact_catalog()
        memory_ctx = self._past_decisions(task)
        system = (
            "You are the router for a meta-orchestrator that picks the best AI agent "
            "for a given task from a curated catalog. You will be given the task and "
            "the catalog of available agents (with name, ecosystem, category, "
            "description, and capabilities). Pick the single best primary agent. "
            "Optionally pick 1-3 supporting agents that should run in parallel or "
            "sequentially to improve the result. Always cite which agents you chose "
            "and why in the 'reasoning' field. If multiple agents are needed, set "
            "'mode' to 'sequential' (handoff in order), 'parallel' (run together, "
            "merge results), or 'evaluator-optimizer' (one generates, one critiques). "
            "Otherwise use 'single'."
        )
        user = (
            f"## Task\n{task}\n\n"
            f"## Past routing decisions for similar tasks\n{memory_ctx or '(none yet)'}\n\n"
            f"## Available agents\n{catalog}\n\n"
            "Return your answer by calling the `route` tool."
        )

        tool = {
            "name": "route",
            "description": "Submit the routing decision",
            "input_schema": {
                "type": "object",
                "properties": {
                    "primary": {"type": "string", "description": "Name of the primary agent (must exist in the catalog)"},
                    "supporting": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of 0-3 supporting agents",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["single", "sequential", "parallel", "evaluator-optimizer"],
                    },
                    "reasoning": {"type": "string", "description": "Why you picked these agents"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["primary", "mode", "reasoning", "confidence"],
            },
        }

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "route"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "route":
                payload = block.input or {}
                primary = payload.get("primary", "")
                if not self.registry.get(primary):
                    # Hallucinated name — degrade to heuristic but keep the LLM's intent
                    fallback = self._heuristic_route(task)
                    fallback.reasoning = f"LLM chose unknown agent '{primary}'; fell back. Original reasoning: {payload.get('reasoning', '')}"
                    return fallback
                return RoutingDecision(
                    primary=primary,
                    supporting=[s for s in payload.get("supporting", []) if self.registry.get(s)],
                    mode=payload.get("mode", "single"),
                    reasoning=payload.get("reasoning", ""),
                    confidence=float(payload.get("confidence", 0.5)),
                )
        raise RuntimeError("Router LLM did not return a tool_use block")

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_route(self, task: str) -> RoutingDecision:
        ranked = self.registry.search(task, limit=4)
        if not ranked:
            # Last resort — pick a generalist
            generalist = (
                self.registry.get("fullstack-developer")
                or self.registry.get("research-analyst")
                or self.registry.agents[0]
            )
            return RoutingDecision(
                primary=generalist.name,
                mode="single",
                reasoning="No keyword match — defaulting to generalist",
                confidence=0.2,
            )
        primary = ranked[0]
        supporting = [a.name for a in ranked[1:3]]
        return RoutingDecision(
            primary=primary.name,
            supporting=supporting,
            mode="single" if not supporting else "parallel",
            reasoning=f"Heuristic match on capabilities: {', '.join(primary.capabilities[:3])}",
            confidence=0.5,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compact_catalog(self) -> str:
        lines: list[str] = []
        for a in self.registry.agents:
            caps = ",".join(a.capabilities[:6])
            lines.append(
                f"- {a.name} [{a.ecosystem.value}/{a.category}] caps=[{caps}] — {a.description}"
            )
        return "\n".join(lines)

    def _past_decisions(self, task: str, k: int = 3) -> str:
        """Assemble few-shot context: prior routing decisions AND their outcomes.

        Pulling outcomes alongside decisions lets the LLM router notice that
        agent X failed last time on a similar task and pick something else --
        the feedback loop the v1 router was missing.
        """
        if self.memory is None:
            return ""
        from forgent.memory.store import MemoryType
        routing_entries = self.memory.recall(task, limit=k, type=MemoryType.ROUTING)
        outcome_entries = self.memory.recall(task, limit=k, type=MemoryType.OUTCOME)
        lines: list[str] = []
        if routing_entries:
            lines.append("Prior routing decisions:")
            lines.extend(f"- {e.content}" for e in routing_entries)
        if outcome_entries:
            if lines:
                lines.append("")
            lines.append("Prior outcomes (factor these in -- prefer agents that succeeded):")
            lines.extend(f"- {e.content}" for e in outcome_entries)
        return "\n".join(lines)
