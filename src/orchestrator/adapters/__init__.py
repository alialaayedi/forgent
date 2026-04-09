"""Ecosystem adapters — translate the orchestrator's neutral (agent, task, context)
shape to whatever the underlying framework expects, and back."""

from orchestrator.adapters.base import Adapter, AdapterResult
from orchestrator.adapters.claude_code import ClaudeCodeAdapter
from orchestrator.adapters.python_framework import PythonFrameworkAdapter
from orchestrator.adapters.mcp_server import MCPAdapter

__all__ = [
    "Adapter",
    "AdapterResult",
    "ClaudeCodeAdapter",
    "PythonFrameworkAdapter",
    "MCPAdapter",
]
