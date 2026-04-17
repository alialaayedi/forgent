"""Planner — turns (task, routing decision, knowledge pack) into a PlanCard.

The planner is the heart of forgent. Where v1 was a persona router, v2 is
a planning layer: `task -> router -> planner -> PlanCard -> host executes`.

v0.3 adds **progressive memory**: instead of dumping a big recalled-memory
string into every PlanCard, the planner returns a **memory index** -- a
handful of virtual paths (e.g. `/outcomes/backbone/`, `/notes/auth/`) the
host can browse on demand via the `memory_view` MCP tool. Shape inspired
by Anthropic's memory_20250818 tool protocol; it lets Opus 4.7's long-
horizon agentic strengths drill into only what's relevant to the current
step, instead of parsing a wall of context up front.

Uses Anthropic structured tool-use when an API key is available; otherwise
falls back to a deterministic heuristic so the system still works offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forgent.memory.store import MemoryEntry, MemoryStore
    from forgent.registry.loader import AgentSpec, Registry
    from forgent.router.router import RoutingDecision

PLANNER_MODEL_DEFAULT = "claude-haiku-4-5-20251001"

# Cap on the recalled_memory *preview* we keep on the card. The host pulls
# detail via memory_view -- we only need enough to let the model decide
# whether a path looks relevant.
_RECALL_PREVIEW_CHARS = 800


@dataclass
class MemoryPath:
    """One entry in the PlanCard's memory index.

    Points at a virtual path in the MemoryStore. The host is expected to
    call `memory_view(path)` if this entry looks relevant to its current
    step. Keeping this struct small keeps the PlanCard compact.
    """

    path: str
    label: str
    count: int = 0

    def to_dict(self) -> dict:
        return {"path": self.path, "label": self.label, "count": self.count}


@dataclass
class PlanCard:
    """Structured output the host LLM consults to execute a task.

    The card is a *contract*, not a persona. It does not say "you are X"; it
    says "here's what done looks like, here's how to break it down, here's
    what usually goes wrong, here's where prior context lives". The host
    LLM reads it and proceeds with its own tools.

    Memory is surfaced two ways:
      - `memory_index` -- a handful of virtual paths the host can drill
        into via the `memory_view` MCP tool. This is the primary surface.
      - `recalled_memory` -- a short preview the host sees inline so it
        can decide which index paths to open. Capped at ~800 chars.
      - `past_outcomes` -- one-liner summaries of recent wins/losses for
        this agent, included inline because they're both short and always
        relevant.
    """

    task: str
    session_id: str

    # --- routing ---
    primary_agent: str
    supporting: list[str] = field(default_factory=list)
    confidence: float = 0.0
    routing_reasoning: str = ""
    knowledge_pack_summary: str = ""

    # --- decomposition ---
    steps: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)

    # --- memory (progressive) ---
    memory_index: list[MemoryPath] = field(default_factory=list)
    recalled_memory: str = ""  # preview, not a dump
    past_outcomes: list[str] = field(default_factory=list)

    # --- provenance ---
    forged: bool = False
    heuristic: bool = False  # true when LLM planner unavailable

    # ------------------------------------------------------------------

    def assignment_block(self) -> str:
        """The visible card the host is instructed to echo to the user."""
        sup_str = f" + {', '.join(self.supporting)}" if self.supporting else ""
        forged_tag = " | newly forged" if self.forged else ""
        mode_tag = " | heuristic" if self.heuristic else ""
        return (
            "```\n"
            "forgent -- plan card ----------------------------------\n"
            f"  knowledge:   {self.primary_agent}{sup_str}{forged_tag}\n"
            f"  confidence:  {self.confidence:.0%}{mode_tag}\n"
            f"  reason:      {self.routing_reasoning}\n"
            f"  session:     {self.session_id[:8]}\n"
            "-------------------------------------------------------\n"
            "```"
        )

    def to_markdown(self) -> str:
        """Full response body returned from the ``advise_task`` MCP tool."""
        parts: list[str] = [self.assignment_block()]

        parts.append(
            "## DISPLAY THE BLOCK ABOVE TO THE USER\n\n"
            "Copy the forgent plan card above into your response verbatim so "
            "the user sees which knowledge pack was chosen. Then execute the "
            "task below using your own tools, consulting the plan as a guide. "
            "You are NOT adopting a persona -- you are working from a curated "
            "plan that forgent built for this specific task.\n\n"
            "**Memory is progressive.** The index below lists virtual paths "
            "into forgent's project memory. Call `memory_view(path)` on any "
            "path that looks relevant to your current step -- do not pull "
            "everything up front. When you discover something future sessions "
            "should know (file locations, conventions, gotchas), call "
            "`memory_write(\"/notes/<topic>\", \"...\")` to leave a breadcrumb.\n\n"
            "When the task is complete (success OR failure), call "
            "`report_outcome` with the session id above so routing improves "
            "over time.\n\n"
            f"**Task:** {self.task}"
        )

        if self.knowledge_pack_summary:
            parts.append(
                "## Knowledge pack\n\n"
                f"_Synthesized from `{self.primary_agent}` for this task._\n\n"
                f"{self.knowledge_pack_summary}"
            )

        if self.steps:
            steps_md = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(self.steps))
            parts.append(f"## Plan\n\n{steps_md}")

        if self.gotchas:
            gotchas_md = "\n".join(f"- {g}" for g in self.gotchas)
            parts.append(f"## Gotchas\n\n{gotchas_md}")

        if self.success_criteria:
            sc_md = "\n".join(f"- {c}" for c in self.success_criteria)
            parts.append(f"## Success criteria\n\n{sc_md}")

        if self.past_outcomes:
            outcomes_md = "\n".join(f"- {o}" for o in self.past_outcomes)
            parts.append(f"## Past outcomes on similar tasks\n\n{outcomes_md}")

        # Memory index -- the primary memory surface in v0.3.
        if self.memory_index:
            idx_md = "\n".join(
                f"- `{m.path}` -- {m.label}" for m in self.memory_index
            )
            parts.append(
                "## Memory index\n\n"
                "Browse any path that looks relevant via `memory_view(path)`. "
                "Don't open everything -- pull what you need, when you need it.\n\n"
                f"{idx_md}"
            )
        else:
            parts.append(
                "## Memory index\n\n_no prior memory for this project yet_"
            )

        # Inline preview only -- keep the card compact.
        if self.recalled_memory:
            parts.append(
                "## Recalled memory (preview)\n\n"
                "_Use `memory_view` on the index paths above for the full content._\n\n"
                f"{self.recalled_memory}"
            )

        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "session_id": self.session_id,
            "primary_agent": self.primary_agent,
            "supporting": self.supporting,
            "confidence": self.confidence,
            "routing_reasoning": self.routing_reasoning,
            "knowledge_pack_summary": self.knowledge_pack_summary,
            "steps": self.steps,
            "gotchas": self.gotchas,
            "success_criteria": self.success_criteria,
            "memory_index": [m.to_dict() for m in self.memory_index],
            "past_outcomes": self.past_outcomes,
            "forged": self.forged,
            "heuristic": self.heuristic,
        }


class Planner:
    """Builds PlanCards from tasks.

    The planner is stateless -- it's given the already-routed decision and
    the agent knowledge pack body, and it produces a structured plan. Memory
    recall, outcome lookup, and memory-index construction are handled by the
    caller (Orchestrator) so the planner is easy to unit-test with synthetic
    inputs.
    """

    def __init__(
        self,
        registry: "Registry",
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.registry = registry
        self.model = model or os.environ.get("FORGENT_PLANNER_MODEL", PLANNER_MODEL_DEFAULT)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None
        if self.api_key:
            try:
                import anthropic  # noqa: WPS433
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except Exception:
                self._client = None

    # ------------------------------------------------------------------

    def plan(
        self,
        task: str,
        session_id: str,
        decision: "RoutingDecision",
        agent: "AgentSpec",
        recalled_memory: str = "",
        past_outcomes: "list[MemoryEntry] | None" = None,
        memory_index: "list[MemoryPath] | None" = None,
        forged: bool = False,
    ) -> PlanCard:
        """Build a PlanCard. Tries LLM first, falls back to heuristic on any failure."""
        outcomes_summaries = self._summarize_outcomes(past_outcomes or [])
        index = list(memory_index or [])
        recall_preview = _preview_recall(recalled_memory)

        if self._client is not None:
            try:
                return self._llm_plan(
                    task=task,
                    session_id=session_id,
                    decision=decision,
                    agent=agent,
                    recalled_memory=recall_preview,
                    past_outcomes=outcomes_summaries,
                    memory_index=index,
                    forged=forged,
                )
            except Exception:
                pass  # fall through to heuristic

        return self._heuristic_plan(
            task=task,
            session_id=session_id,
            decision=decision,
            agent=agent,
            recalled_memory=recall_preview,
            past_outcomes=outcomes_summaries,
            memory_index=index,
            forged=forged,
        )

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------

    def _llm_plan(
        self,
        task: str,
        session_id: str,
        decision: "RoutingDecision",
        agent: "AgentSpec",
        recalled_memory: str,
        past_outcomes: list[str],
        memory_index: list[MemoryPath],
        forged: bool,
    ) -> PlanCard:
        body = agent.load_body() or ""
        # Keep the knowledge pack bounded -- the planner LLM only needs the
        # distilled shape, not the full voice/persona prose.
        knowledge_excerpt = body[:6000]

        system = (
            "You are the planner for forgent, a meta-orchestrator. Your job: "
            "given a task and a curated knowledge pack for the domain, produce "
            "a concrete plan a capable coding agent can execute. You are NOT "
            "writing a persona or a role description. You are extracting the "
            "domain knowledge and shaping it into actionable guidance for THIS "
            "specific task: concrete steps, known gotchas, measurable success "
            "criteria, and a one-paragraph synthesis of the pack.\n\n"
            "Rules:\n"
            "- Steps must be actionable (imperative verbs, checkable outcomes).\n"
            "- Gotchas must be specific -- name tools, files, or states.\n"
            "- Success criteria must be verifiable (tests pass, output matches, etc).\n"
            "- Synthesis should be 2-4 sentences of dense domain-specific guidance -- "
            "not fluff, not a role card.\n"
            "- If the memory index or past outcomes show prior failures, factor "
            "them into the gotchas without quoting the entire recall -- the host "
            "will pull details via memory_view on demand."
        )

        recalled_block = recalled_memory if recalled_memory else "(none)"
        outcomes_block = (
            "\n".join(f"- {o}" for o in past_outcomes) if past_outcomes else "(none)"
        )
        index_block = (
            "\n".join(f"- {m.path} -- {m.label}" for m in memory_index)
            if memory_index
            else "(no prior memory)"
        )
        user = (
            f"## Task\n{task}\n\n"
            f"## Knowledge pack: {agent.name}\n"
            f"Category: {agent.category}\n"
            f"Capabilities: {', '.join(agent.capabilities)}\n"
            f"Description: {agent.description}\n\n"
            f"Body (source material):\n{knowledge_excerpt}\n\n"
            f"## Memory index (paths the host can drill into)\n{index_block}\n\n"
            f"## Recalled memory (preview)\n{recalled_block}\n\n"
            f"## Past outcomes on similar tasks\n{outcomes_block}\n\n"
            "Return your plan by calling the `submit_plan` tool."
        )

        tool = {
            "name": "submit_plan",
            "description": "Submit the structured plan for this task.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "knowledge_pack_summary": {
                        "type": "string",
                        "description": "2-4 sentences of dense task-specific guidance distilled from the pack body.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-6 concrete, imperative steps. Each step is one line.",
                    },
                    "gotchas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-5 specific things that commonly go wrong in this task class.",
                    },
                    "success_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-5 verifiable conditions that define done.",
                    },
                },
                "required": ["knowledge_pack_summary", "steps", "gotchas", "success_criteria"],
            },
        }

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_plan"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_plan":
                payload = block.input or {}
                return PlanCard(
                    task=task,
                    session_id=session_id,
                    primary_agent=decision.primary,
                    supporting=list(decision.supporting),
                    confidence=decision.confidence,
                    routing_reasoning=decision.reasoning,
                    knowledge_pack_summary=str(payload.get("knowledge_pack_summary", "")).strip(),
                    steps=[str(s) for s in payload.get("steps", []) if s][:8],
                    gotchas=[str(g) for g in payload.get("gotchas", []) if g][:8],
                    success_criteria=[str(c) for c in payload.get("success_criteria", []) if c][:8],
                    memory_index=memory_index,
                    recalled_memory=recalled_memory,
                    past_outcomes=past_outcomes,
                    forged=forged,
                    heuristic=False,
                )
        raise RuntimeError("Planner LLM did not return a submit_plan tool_use block")

    # ------------------------------------------------------------------
    # Heuristic fallback
    # ------------------------------------------------------------------

    def _heuristic_plan(
        self,
        task: str,
        session_id: str,
        decision: "RoutingDecision",
        agent: "AgentSpec",
        recalled_memory: str,
        past_outcomes: list[str],
        memory_index: list[MemoryPath],
        forged: bool,
    ) -> PlanCard:
        caps = agent.capabilities[:6]
        summary = (
            f"{agent.description}. Work from the capability set "
            f"[{', '.join(caps) or 'general'}] and prefer approaches that are "
            "idiomatic for this domain. Verify every claim with a tool call "
            "before writing code or prose about it."
        )
        steps = [
            "Read the task carefully and extract the concrete deliverable and constraints.",
            "Inspect relevant files/state with your tools before making any changes.",
            "Implement the change in the smallest coherent unit that satisfies the task.",
            "Verify the change (run tests, re-read the file, exercise the feature).",
            "Summarize what was done and any followups, then call report_outcome.",
        ]
        gotchas = [
            "Do not rely on the knowledge pack's prose alone -- ground every claim in the current codebase.",
            "Check for conventions already present in the repo before introducing new patterns.",
        ]
        if caps:
            gotchas.append(
                f"Common pitfalls in this domain: unchecked assumptions around {caps[0]}."
            )
        if memory_index:
            gotchas.append(
                "Check the memory index paths for prior notes/outcomes before improvising."
            )
        success_criteria = [
            "Deliverable produced and matches task intent.",
            "No regressions in existing tests.",
            "Changes are minimal and focused on the task.",
        ]
        return PlanCard(
            task=task,
            session_id=session_id,
            primary_agent=decision.primary,
            supporting=list(decision.supporting),
            confidence=decision.confidence,
            routing_reasoning=decision.reasoning,
            knowledge_pack_summary=summary,
            steps=steps,
            gotchas=gotchas,
            success_criteria=success_criteria,
            memory_index=memory_index,
            recalled_memory=recalled_memory,
            past_outcomes=past_outcomes,
            forged=forged,
            heuristic=True,
        )

    # ------------------------------------------------------------------

    def _summarize_outcomes(self, outcomes: "list[MemoryEntry]") -> list[str]:
        """Render outcome entries as short strings for display + LLM context."""
        out: list[str] = []
        for e in outcomes[:6]:
            out.append(e.content.strip())
        return out


def _preview_recall(recalled: str) -> str:
    """Shrink a long recall dump to a preview. The index is the real surface."""
    if not recalled:
        return ""
    if len(recalled) <= _RECALL_PREVIEW_CHARS:
        return recalled
    return recalled[: _RECALL_PREVIEW_CHARS - 3].rstrip() + "..."
