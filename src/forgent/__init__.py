"""forgent — planning + knowledge layer for AI coding agents.

You give forgent a task. It routes to the best-matching curated knowledge
pack from 60+ specialists, pulls relevant memory and prior outcomes, and
returns a structured PlanCard (steps, gotchas, success criteria, recalled
context). The host LLM executes the plan with its own tools, then calls
record_outcome to close the feedback loop. When no curated pack fits, the
AgentForge synthesizes a new one on demand.
"""

from forgent.orchestrator import Orchestrator
from forgent.planner import MemoryPath, PlanCard, Planner
from forgent.registry.forge import AgentForge, ForgedAgent
from forgent.registry.loader import AgentSpec, Registry
from forgent.router.router import Router, RoutingDecision

__version__ = "0.3.0"
__all__ = [
    "Orchestrator",
    "Planner",
    "PlanCard",
    "MemoryPath",
    "Registry",
    "AgentSpec",
    "AgentForge",
    "ForgedAgent",
    "Router",
    "RoutingDecision",
]
