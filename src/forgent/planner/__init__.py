"""Planning layer — turns a task + routing decision into a structured PlanCard.

This is the shift from "swap a persona and hope" to "hand the host LLM a
concrete plan, the relevant domain checklists, and prior outcomes". The
host Claude keeps its own tools and context window; forgent contributes
decomposition, memory, and a curated knowledge pack.
"""

from forgent.planner.planner import PlanCard, Planner

__all__ = ["PlanCard", "Planner"]
