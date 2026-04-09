"""Agent Orchestrator — a meta-router across Claude Code subagents,
Python multi-agent frameworks, and MCP servers.

The big idea: you don't pick a framework, you pick a task. The orchestrator
classifies the task, finds the best curated agent for it (across ecosystems),
and runs it — handing off shared state if multiple agents are needed.
"""

from orchestrator.registry.loader import Registry, AgentSpec
from orchestrator.router.router import Router, RoutingDecision
from orchestrator.orchestrator import Orchestrator

__version__ = "0.1.0"
__all__ = ["Orchestrator", "Registry", "AgentSpec", "Router", "RoutingDecision"]
