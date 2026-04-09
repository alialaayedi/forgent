"""forgent — a meta-orchestrator that routes tasks across Claude Code
subagents, Python multi-agent frameworks, and MCP servers, and forges
brand-new specialist subagents on demand.

The big idea: you don't pick a framework, you pick a task. forgent classifies
the task, finds the best curated agent for it (across ecosystems), and runs
it — handing off shared state if multiple agents are needed. When no curated
agent fits, it grows a new specialist via the AgentForge.
"""

from forgent.registry.loader import Registry, AgentSpec
from forgent.registry.forge import AgentForge, ForgedAgent
from forgent.router.router import Router, RoutingDecision
from forgent.orchestrator import Orchestrator

__version__ = "0.1.0"
__all__ = [
    "Orchestrator",
    "Registry",
    "AgentSpec",
    "AgentForge",
    "ForgedAgent",
    "Router",
    "RoutingDecision",
]
