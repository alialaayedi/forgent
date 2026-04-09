"""Ecosystem adapters — translate the orchestrator's neutral (agent, task, context)
shape to whatever the underlying framework expects, and back."""

from forgent.adapters.base import Adapter, AdapterResult
from forgent.adapters.claude_code import ClaudeCodeAdapter
from forgent.adapters.python_framework import PythonFrameworkAdapter
from forgent.adapters.mcp_server import MCPAdapter

__all__ = [
    "Adapter",
    "AdapterResult",
    "ClaudeCodeAdapter",
    "PythonFrameworkAdapter",
    "MCPAdapter",
]
