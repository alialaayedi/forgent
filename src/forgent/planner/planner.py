"""Planner — turns (task, routing decision, knowledge pack) into a PlanCard.

The planner is the heart of forgent. Where v1 was a persona router, v2 is
a planning layer: `task -> router -> planner -> PlanCard -> host executes`.

v0.3 adds **progressive memory**: instead of dumping a big recalled-memory
string into every PlanCard, the planner returns a **memory index** -- a
handful of virtual paths (e.g. `/outcomes/backbone/`, `/notes/auth/`) the
host can browse on demand via the `memory_view` MCP tool. Shape inspired
by Anthropic's memory_20250818 tool protocol; it lets the host model drill
into only what's relevant to the current step, instead of parsing a wall of
context up front.

Uses Anthropic structured outputs when an API key is available; otherwise
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

from forgent.llm import PLANNER, PLANNER_MODEL_DEFAULT, make_client, structured_call  # noqa: F401 (re-exported)

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
    alternates: list[dict] = field(default_factory=list)  # top-3 runners-up

    # --- decomposition ---
    steps: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)

    # --- memory (progressive) ---
    memory_index: list[MemoryPath] = field(default_factory=list)
    recalled_memory: str = ""  # preview, not a dump
    past_outcomes: list[str] = field(default_factory=list)

    # --- multi-agent plan graph ---
    subplans: list["PlanCard"] = field(default_factory=list)  # DAG of child plans
    handoff_contract: str = ""  # what the primary passes to each child

    # --- budgeting & revision ---
    version: int = 1                     # v2 / v3 after revise_plan
    budget_ms: int | None = None         # honored by planner
    budget_usd: float | None = None      # honored by planner

    # --- provenance ---
    forged: bool = False
    heuristic: bool = False  # true when LLM planner unavailable

    # ------------------------------------------------------------------

    def assignment_block(self) -> str:
        """The visible card the host is instructed to echo to the user.

        Open on the right so it never misaligns when a font renders the
        block-drawing glyphs at a different width than ASCII.
        """
        tags = [t for t, on in (("forged", self.forged), ("heuristic", self.heuristic)) if on]
        tag_str = f"  · {' · '.join(tags)}" if tags else ""
        pack = self.primary_agent + (f"  + {', '.join(self.supporting)}" if self.supporting else "")
        why = _wrap(self.routing_reasoning or "-", width=64)
        why_lines = [f"│  why         {why[0]}"] + [f"│              {line}" for line in why[1:]]
        summary = [
            _count(len(self.steps), "step"),
            _count(len(self.gotchas), "gotcha"),
            _count(len(self.success_criteria), "check"),
            f"memory {_count(len(self.memory_index), 'path')}" if self.memory_index else "no memory yet",
        ]
        lines = [
            f"╭─ forgent · plan card ─── {self.session_id[:8]}",
            f"│  pack        {pack}",
            f"│  confidence  {_bar(self.confidence)}  {self.confidence:.0%}{tag_str}",
            *why_lines,
            f"╰─ {' · '.join(summary)}",
        ]
        return "```\n" + "\n".join(lines) + "\n```"

    def to_markdown(self) -> str:
        """Full response body returned from the ``advise_task`` MCP tool.

        Order: the card, the task, the plan itself (what the host acts on),
        memory, then the secondary material and the usage protocol last.
        """
        parts: list[str] = [self.assignment_block(), f"**Task:** {self.task}"]

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
            sc_md = "\n".join(f"- [ ] {c}" for c in self.success_criteria)
            parts.append(f"## Success criteria\n\n{sc_md}")

        if self.past_outcomes:
            outcomes_md = "\n".join(f"- {o}" for o in self.past_outcomes)
            parts.append(f"## Past outcomes on similar tasks\n\n{outcomes_md}")

        # Memory index -- the primary memory surface since v0.3.
        if self.memory_index:
            idx_md = "\n".join(
                f"- `{m.path}` -- {m.label}" for m in self.memory_index
            )
            parts.append(
                "## Memory index\n\n"
                "Open a path with `memory_view(path)` when it is relevant to your "
                "current step.\n\n"
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

        if self.alternates:
            # Collapsed: users only open it when they wonder WHY this pack.
            alts_md = "\n".join(
                f"- **{a.get('name', '')}** ({int(a.get('score', 0) * 100)}%) -- "
                f"{a.get('reasoning', '')}"
                for a in self.alternates
            )
            parts.append(
                "## Why this pack?\n\n"
                f"**Picked:** `{self.primary_agent}` -- {self.routing_reasoning}\n\n"
                "<details>\n<summary>Other candidates considered</summary>\n\n"
                f"{alts_md}\n\n</details>"
            )

        if self.subplans:
            # Multi-agent plan graph: render each child as a nested block.
            # The handoff_contract explains what the parent hands off to the
            # children -- the glue between plans.
            handoff = (
                f"\n**Handoff contract:** {self.handoff_contract}\n\n"
                if self.handoff_contract else "\n"
            )
            blocks: list[str] = [
                "## Sub-plans\n\n"
                "This task spans multiple domains. Each sub-plan below is a "
                "self-contained PlanCard for one supporting specialist. Execute "
                "them in the order the primary plan specifies."
                + handoff
            ]
            for idx, sub in enumerate(self.subplans, 1):
                sub_md = sub.to_markdown()
                blocks.append(
                    f"<details>\n<summary>{idx}. `{sub.primary_agent}` -- "
                    f"{sub.knowledge_pack_summary[:120]}</summary>\n\n"
                    f"{sub_md}\n\n</details>"
                )
            parts.append("\n\n".join(blocks))

        parts.append(
            "## How to use this card\n\n"
            "Show the plan card block at the top to the user so they can see "
            "which knowledge pack was chosen, then execute the task with your "
            "own tools, using the plan as your guide. This is a curated plan "
            "for this specific task, not a persona.\n\n"
            "- **Memory is progressive.** Open index paths with `memory_view` "
            "when relevant rather than pulling everything up front.\n"
            "- **Leave breadcrumbs.** When you discover something future sessions "
            "should know (file locations, conventions, gotchas), call "
            "`memory_write(\"/notes/<topic>\", \"...\")`.\n"
            f"- **Close the loop.** When the task ends, successfully or not, call "
            f"`report_outcome` with session id `{self.session_id}` so routing "
            "improves over time."
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
        self.model = PLANNER.model(model)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = make_client(self.api_key)

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
        budget_ms: int | None = None,
        budget_usd: float | None = None,
        _recursion_depth: int = 0,
    ) -> PlanCard:
        """Build a PlanCard. Tries LLM first, falls back to heuristic on any failure.

        When the routing decision picks `mode in (sequential, parallel,
        evaluator-optimizer)`, the planner recursively builds a sub-PlanCard
        per supporting agent (DAG depth capped at _MAX_SUBPLAN_DEPTH). This is
        the v0.4 multi-agent plan graph -- a direct answer to Claude Flow /
        BMAD's multi-agent teams.

        Budgets:
          - budget_ms: if set AND the LLM call would likely exceed it
            (rough estimate based on token count + model latency), fall back
            to heuristic mode for this plan and don't recurse.
          - budget_usd: if set AND the planner model would cost more than
            this, downshift to heuristic.
        """
        outcomes_summaries = self._summarize_outcomes(past_outcomes or [])
        index = list(memory_index or [])
        recall_preview = _preview_recall(recalled_memory)

        # Budget check: tight latency/cost -> skip the LLM path.
        use_heuristic = self._client is None or _budget_too_tight(
            budget_ms, budget_usd, self.model
        )

        if not use_heuristic:
            try:
                card = self._llm_plan(
                    task=task,
                    session_id=session_id,
                    decision=decision,
                    agent=agent,
                    recalled_memory=recall_preview,
                    past_outcomes=outcomes_summaries,
                    memory_index=index,
                    forged=forged,
                )
                card.budget_ms = budget_ms
                card.budget_usd = budget_usd
                if _recursion_depth < _MAX_SUBPLAN_DEPTH and decision.mode != "single":
                    card.subplans = self._recurse_subplans(
                        task=task,
                        decision=decision,
                        session_id=session_id,
                        recalled_memory=recall_preview,
                        past_outcomes=outcomes_summaries,
                        memory_index=index,
                        budget_ms=_halve(budget_ms),
                        budget_usd=_halve(budget_usd),
                        depth=_recursion_depth + 1,
                    )
                    card.handoff_contract = _handoff_contract(agent, decision)
                return card
            except Exception:
                pass  # fall through to heuristic

        card = self._heuristic_plan(
            task=task,
            session_id=session_id,
            decision=decision,
            agent=agent,
            recalled_memory=recall_preview,
            past_outcomes=outcomes_summaries,
            memory_index=index,
            forged=forged,
        )
        card.budget_ms = budget_ms
        card.budget_usd = budget_usd
        return card

    def _recurse_subplans(
        self,
        task: str,
        decision: "RoutingDecision",
        session_id: str,
        recalled_memory: str,
        past_outcomes: list[str],
        memory_index: list[MemoryPath],
        budget_ms: int | None,
        budget_usd: float | None,
        depth: int,
    ) -> list[PlanCard]:
        """Generate one sub-PlanCard per supporting agent."""
        out: list[PlanCard] = []
        for name in decision.supporting:
            sub_agent = self.registry.get(name)
            if sub_agent is None:
                continue
            # Build a narrower routing decision for the child.
            from forgent.router.router import RoutingDecision as _RD
            child_decision = _RD(
                primary=name,
                supporting=[],
                mode="single",
                reasoning=f"Sub-plan for {name}, spawned by {decision.primary}",
                confidence=decision.confidence,
                alternates=[],
            )
            child = self.plan(
                task=f"[sub-plan of '{task[:80]}'] {sub_agent.description}",
                session_id=session_id,
                decision=child_decision,
                agent=sub_agent,
                recalled_memory=recalled_memory,
                past_outcomes=[],  # already surfaced on the parent
                memory_index=[],   # parent already has the index
                forged=False,
                budget_ms=budget_ms,
                budget_usd=budget_usd,
                _recursion_depth=depth,
            )
            out.append(child)
        return out

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
            f"## Past outcomes on similar tasks\n{outcomes_block}"
        )

        schema = {
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
        }

        payload = structured_call(
            self._client,
            role=PLANNER,
            model=self.model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=16000,
        )
        return PlanCard(
            task=task,
            session_id=session_id,
            primary_agent=decision.primary,
            supporting=list(decision.supporting),
            confidence=decision.confidence,
            routing_reasoning=decision.reasoning,
            alternates=[a.to_dict() for a in decision.alternates],
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
            alternates=[a.to_dict() for a in decision.alternates],
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


def _bar(value: float, width: int = 10) -> str:
    """Unicode meter for the card, e.g. 0.82 -> ########.. (block glyphs)."""
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(" ".join(text.split()), width=width) or ["-"]


def _preview_recall(recalled: str) -> str:
    """Shrink a long recall dump to a preview. The index is the real surface."""
    if not recalled:
        return ""
    if len(recalled) <= _RECALL_PREVIEW_CHARS:
        return recalled
    return recalled[: _RECALL_PREVIEW_CHARS - 3].rstrip() + "..."


# --------------------------------------------------------------------------- budgets

# Recursion cap on subplans. A DAG deeper than this rarely helps and burns
# tokens fast.
_MAX_SUBPLAN_DEPTH = 1  # parent + one layer of children

# Rough latency estimates (ms) for one planner call at typical token counts
# and the default effort. Used only when the caller passes budget_ms.
_MODEL_LATENCY_MS: dict[str, int] = {
    "claude-fable-5-1": 30_000,
    "claude-opus-5-5": 15_000,
    "claude-opus-5": 15_000,
    "claude-opus-4-8": 12_000,
    "claude-sonnet-5": 8_000,
    "claude-haiku-4-5": 2_500,
}

# Rough cost estimates in USD for one planner call at ~4k input / ~2k output
# plus adaptive thinking. List prices per MTok (in/out): Fable 5.1 $10/$50,
# Opus 5.5 $4/$20, Opus 5 and 4.8 $5/$25, Sonnet 5 $2/$10, Haiku 4.5 $1/$5.
_MODEL_COST_USD: dict[str, float] = {
    "claude-fable-5-1": 0.20,
    "claude-opus-5-5": 0.08,
    "claude-opus-5": 0.10,
    "claude-opus-4-8": 0.10,
    "claude-sonnet-5": 0.04,
    "claude-haiku-4-5": 0.015,
}


def _budget_too_tight(budget_ms: int | None, budget_usd: float | None, model: str) -> bool:
    """True when the LLM planner would likely miss the caller's budget."""
    if budget_ms is not None and budget_ms < _MODEL_LATENCY_MS.get(model, 10_000):
        return True
    if budget_usd is not None and budget_usd < _MODEL_COST_USD.get(model, 0.08):
        return True
    return False


def _halve(x):
    """Half the remaining budget for the child. Prevents run-away recursion."""
    if x is None:
        return None
    return type(x)(x / 2)


def _handoff_contract(agent, decision) -> str:
    """Short sentence describing what the primary hands off to each child."""
    if not decision.supporting:
        return ""
    mode = decision.mode
    if mode == "sequential":
        return (
            f"Primary `{agent.name}` completes its plan, then hands off to "
            f"{' -> '.join(decision.supporting)} in order. Each child receives "
            "the primary's output as prior context."
        )
    if mode == "parallel":
        return (
            f"Primary `{agent.name}` and {', '.join(decision.supporting)} run "
            "in parallel on the same task. Merge their outputs before reporting."
        )
    if mode == "evaluator-optimizer":
        return (
            f"Primary `{agent.name}` drafts; {decision.supporting[0] if decision.supporting else '(none)'} "
            "critiques and the primary revises until the critique passes."
        )
    return ""
