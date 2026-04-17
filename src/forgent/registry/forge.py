"""AgentForge — synthesizes new specialist subagents on demand.

The orchestrator can grow its own specialists. When it sees a task that
doesn't have a strong fit in the curated catalog, it asks the LLM to
*design* a new agent for that task — a name, category, capabilities,
description, and a full system prompt — and persists the result so it's
available for every future task of the same shape.

Storage:
    src/orchestrator/registry/dynamic.yaml          ← spec metadata (appended)
    src/orchestrator/registry/agents/dynamic/<name>.md ← system prompt body

The forged agent is added to the in-memory `Registry` immediately and shows
up in `list_agents`, `search_agents`, and the router on the next call.

Determinism: forging is *opt-in*. The orchestrator never auto-spawns agents
unless `auto_forge=True` is set on `run_task`, or the user explicitly calls
`forge_agent` (CLI or MCP tool).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from forgent.registry.loader import (
    PKG_DIR,
    AgentSpec,
    Ecosystem,
    Registry,
)

if TYPE_CHECKING:
    pass

DYNAMIC_CATALOG = PKG_DIR / "dynamic.yaml"
DYNAMIC_AGENTS_DIR = PKG_DIR / "agents" / "claude_code"

FORGE_MODEL_DEFAULT = "claude-opus-4-7"


@dataclass
class ForgedAgent:
    spec: AgentSpec
    body: str
    is_new: bool


class AgentForge:
    """Generates new specialist subagents via Claude.

    Usage:
        forge = AgentForge(registry)
        forged = await forge.forge("write Solidity smart contracts with formal verification")
        # forged.spec is a new AgentSpec, already added to `registry`
    """

    def __init__(
        self,
        registry: Registry,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.registry = registry
        self.model = model or os.environ.get("FORGENT_FORGE_MODEL", FORGE_MODEL_DEFAULT)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None
        if self.api_key:
            try:
                import anthropic  # noqa: WPS433
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self._client = None

    # ------------------------------------------------------------------

    async def forge(
        self,
        task: str,
        name: str | None = None,
        category: str | None = None,
        force: bool = False,
    ) -> ForgedAgent:
        """Synthesize a new specialist agent for the given task.

        Args:
            task: The task or task class the agent should handle.
            name: Optional explicit name. If omitted, Claude picks one.
            category: Optional category hint. If omitted, Claude picks one.
            force: If False (default), reuses an existing dynamic agent with
                   the same generated name. If True, always creates a new one.

        Returns:
            ForgedAgent (spec, body, is_new). On API/key failure, falls back
            to a deterministic stub agent so the system never crashes.
        """
        if self._client is None:
            return self._stub(task, name, category)

        spec_dict = self._call_llm(task, name, category)
        agent_name = _slug(spec_dict.get("name") or name or "specialist")

        # If a dynamic agent with this name already exists and force=False, return it.
        existing = self.registry.get(agent_name)
        if existing is not None and not force:
            existing.load_body()
            return ForgedAgent(spec=existing, body=existing.system_prompt, is_new=False)

        body = spec_dict.get("system_prompt", "").strip()
        if not body:
            return self._stub(task, name, category)

        spec = AgentSpec(
            name=agent_name,
            ecosystem=Ecosystem.CLAUDE_CODE,
            category=category or spec_dict.get("category", "dynamic"),
            description=spec_dict.get("description", f"Forged for: {task[:80]}"),
            capabilities=list(spec_dict.get("capabilities", []))[:8],
            source_repo="forge",
            source_path=f"dynamic/{agent_name}.md",
            model=spec_dict.get("model", "sonnet"),
            kind="agent",
            system_prompt=body,
        )
        self._persist(spec, body)
        self._register_in_memory(spec)
        return ForgedAgent(spec=spec, body=body, is_new=True)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, task: str, name: str | None, category: str | None) -> dict:
        existing_names = sorted(a.name for a in self.registry.agents)
        existing_summary = "\n".join(f"  - {n}" for n in existing_names[:60])

        system = (
            "You are the AgentForge for a meta-orchestrator. Your job: design a new "
            "specialist subagent for a task that the existing curated catalog does not "
            "cover well. Output a complete, production-ready subagent definition. "
            "Be specific — the system prompt should make the agent good at this exact "
            "task class, not just 'a helpful assistant'. Match the style of high-quality "
            "Claude Code subagents: a clear role definition, an explicit checklist of "
            "what to do when invoked, structured headers for capabilities, and a final "
            "communication protocol section."
        )
        user = (
            f"## Task class to specialize for\n{task}\n\n"
            f"## Existing agent names (do not duplicate)\n{existing_summary}\n\n"
            f"## Constraints\n"
            f"- name: {name or '(you pick — short, hyphen-separated, ends in -specialist or -expert if appropriate)'}\n"
            f"- category: {category or '(you pick from: core-development, language-specialist, infrastructure, quality-security, data-ai, developer-experience, specialized, business-product, meta-orchestration, research-analysis, dynamic)'}\n\n"
            "Return your design by calling the `submit_agent` tool."
        )

        tool = {
            "name": "submit_agent",
            "description": "Submit the forged agent definition.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "short hyphen-separated name"},
                    "category": {"type": "string"},
                    "description": {"type": "string", "description": "one-sentence trigger description, used by routing"},
                    "capabilities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-8 short capability tags (e.g. 'graphql', 'subscriptions', 'federation')",
                    },
                    "model": {"type": "string", "enum": ["opus", "sonnet", "haiku"], "description": "model tier"},
                    "system_prompt": {
                        "type": "string",
                        "description": "the full system prompt for this specialist — at least 400 words, structured with markdown headers",
                    },
                },
                "required": ["name", "category", "description", "capabilities", "system_prompt"],
            },
        }

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_agent"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_agent":
                return block.input or {}
        raise RuntimeError("AgentForge LLM did not return a tool_use block")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, spec: AgentSpec, body: str) -> None:
        # 1. Vendor the markdown body
        DYNAMIC_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        md_path = DYNAMIC_AGENTS_DIR / f"{spec.name}.md"
        frontmatter = {
            "name": spec.name,
            "description": spec.description,
            "model": spec.model or "sonnet",
            "tools": "Read, Write, Edit, Bash, Glob, Grep",
        }
        fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        md_path.write_text(f"---\n{fm_yaml}\n---\n\n{body}\n", encoding="utf-8")

        # 2. Append to dynamic.yaml so it survives restarts
        existing = []
        if DYNAMIC_CATALOG.exists():
            data = yaml.safe_load(DYNAMIC_CATALOG.read_text(encoding="utf-8")) or {}
            existing = data.get("agents", [])
        # Replace if already present, otherwise append
        existing = [e for e in existing if e.get("name") != spec.name]
        existing.append(
            {
                "name": spec.name,
                "ecosystem": spec.ecosystem.value,
                "category": spec.category,
                "description": spec.description,
                "capabilities": spec.capabilities,
                "source_repo": spec.source_repo,
                "source_path": spec.source_path,
                "model": spec.model,
                "kind": spec.kind,
                "forged": True,
            }
        )
        DYNAMIC_CATALOG.write_text(
            yaml.safe_dump({"agents": existing}, sort_keys=False),
            encoding="utf-8",
        )

    def _register_in_memory(self, spec: AgentSpec) -> None:
        # Replace existing entry if present, otherwise append.
        self.registry.agents = [a for a in self.registry.agents if a.name != spec.name]
        self.registry.agents.append(spec)
        self.registry._by_name[spec.name] = spec

    # ------------------------------------------------------------------
    # Stub for offline / no-API-key
    # ------------------------------------------------------------------

    def _stub(self, task: str, name: str | None, category: str | None) -> ForgedAgent:
        slug = _slug(name or f"specialist-{abs(hash(task)) % 10000}")
        body = (
            f"You are {slug}, a specialist forged by the orchestrator for the following task class:\n\n"
            f"> {task}\n\n"
            f"## When invoked\n"
            f"1. Read the task carefully and extract the concrete deliverable.\n"
            f"2. Identify constraints, edge cases, and quality bars.\n"
            f"3. Produce the deliverable with explicit reasoning about tradeoffs.\n\n"
            f"## Output format\n"
            f"- Lead with the answer or artifact.\n"
            f"- Follow with brief justification.\n"
            f"- End with any caveats or follow-ups.\n"
        )
        spec = AgentSpec(
            name=slug,
            ecosystem=Ecosystem.CLAUDE_CODE,
            category=category or "dynamic",
            description=f"Forged for: {task[:80]}",
            capabilities=["dynamic"],
            source_repo="forge-stub",
            source_path=f"dynamic/{slug}.md",
            model="sonnet",
            kind="agent",
            system_prompt=body,
        )
        # Don't persist stubs to disk — they're placeholder-quality.
        self._register_in_memory(spec)
        return ForgedAgent(spec=spec, body=body, is_new=True)


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(s: str) -> str:
    s = s.strip().lower().replace(" ", "-").replace("_", "-")
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "specialist"
