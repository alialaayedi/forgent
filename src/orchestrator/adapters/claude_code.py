"""Adapter for Claude Code subagents.

These are markdown files with YAML frontmatter — a system prompt plus optional
tool restrictions. We parse the prompt, tack on the orchestrator's context
block, and call the Anthropic Messages API directly.

Why not invoke Claude Code itself? Two reasons:
  1. Claude Code requires a TTY and an interactive session — not scriptable
     from inside a Python orchestrator loop.
  2. The agent file IS the prompt — it's portable. Running it through the API
     gives us the same behavior with full programmatic control.
"""

from __future__ import annotations

import os

from orchestrator.adapters.base import Adapter, AdapterResult
from orchestrator.registry.loader import AgentSpec, Ecosystem


class ClaudeCodeAdapter(Adapter):
    ecosystem = Ecosystem.CLAUDE_CODE

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 4096):
        self.model = model or os.environ.get("ORCHESTRATOR_MODEL", "claude-opus-4-6")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens
        self._client = None
        if self.api_key:
            try:
                import anthropic  # noqa: WPS433
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self._client = None

    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        if self._client is None:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error="ANTHROPIC_API_KEY not set or anthropic SDK not installed",
            )

        body = agent.load_body() or f"You are {agent.name}, a specialist in {', '.join(agent.capabilities)}."
        # Resolve the model. Frontmatter values like 'sonnet'/'opus'/'haiku' are
        # shorthand — map them to current production model IDs.
        model = _resolve_model(agent.model or self.model)

        system = body
        if context:
            system = f"{body}\n\n## Prior context (from memory)\n{context}"

        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": task}],
            )
        except Exception as exc:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        usage = {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        }
        return AdapterResult(
            agent=agent.name,
            ecosystem=self.ecosystem,
            output=text,
            success=True,
            usage=usage,
        )


_MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "inherit": "claude-opus-4-6",
}


def _resolve_model(name: str) -> str:
    if not name:
        return "claude-opus-4-6"
    if name in _MODEL_ALIASES:
        return _MODEL_ALIASES[name]
    # Strip version dates from older model strings
    if name.startswith("claude-"):
        return name
    return _MODEL_ALIASES.get(name, "claude-opus-4-6")
