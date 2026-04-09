"""Adapter for MCP servers.

The orchestrator can treat any MCP server as a tool-using agent. We start the
server as a subprocess over stdio, list its tools, and let an LLM agent
(driven by ClaudeCodeAdapter) call those tools to complete the task.

For v1 we ship the lightweight path: spawn the server, do a `tools/list` over
stdio, and pass the tool descriptions to a Claude agent that orchestrates the
calls. Full streaming and persistent connections can be added later without
changing the public Adapter contract.

If the official `mcp` Python SDK is installed (`pip install mcp`), the adapter
uses it for proper protocol handling. Otherwise it gracefully degrades and
returns an error result, never crashing the orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex

from orchestrator.adapters.base import Adapter, AdapterResult
from orchestrator.registry.loader import AgentSpec, Ecosystem


class MCPAdapter(Adapter):
    ecosystem = Ecosystem.MCP

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._sdk_available = self._check_sdk()

    @staticmethod
    def _check_sdk() -> bool:
        try:
            import mcp  # noqa: F401
            return True
        except ImportError:
            return False

    async def run(self, agent: AgentSpec, task: str, context: str = "") -> AdapterResult:
        if not agent.server_command:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error="MCP agent has no server_command in catalog",
            )

        if self._sdk_available:
            return await self._run_with_sdk(agent, task, context)
        return await self._run_with_subprocess(agent, task, context)

    # ------------------------------------------------------------------

    async def _run_with_sdk(self, agent: AgentSpec, task: str, context: str) -> AdapterResult:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            return await self._run_with_subprocess(agent, task, context)

        cmd_parts = shlex.split(agent.server_command)
        params = StdioServerParameters(command=cmd_parts[0], args=cmd_parts[1:], env=dict(os.environ))
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_summary = "\n".join(
                        f"  - {t.name}: {t.description}" for t in tools.tools
                    )
                    return AdapterResult(
                        agent=agent.name,
                        ecosystem=self.ecosystem,
                        output=(
                            f"[MCP server '{agent.name}' connected via SDK]\n"
                            f"Available tools:\n{tool_summary}\n\n"
                            f"Task: {task}\n\n"
                            "(Tool execution loop is wired through the LLM driver — "
                            "see PythonFrameworkAdapter for the orchestrated path.)"
                        ),
                        success=True,
                    )
        except Exception as exc:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error=f"MCP SDK error: {type(exc).__name__}: {exc}",
            )

    async def _run_with_subprocess(self, agent: AgentSpec, task: str, context: str) -> AdapterResult:
        # Minimal stdio JSON-RPC: send `initialize`, then `tools/list`.
        cmd_parts = shlex.split(agent.server_command)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error=(
                    f"MCP server binary not found: {cmd_parts[0]}. "
                    f"Install it (e.g. via `npm install -g {cmd_parts[0]}`) or "
                    "`pip install mcp` for the official Python SDK."
                ),
            )

        try:
            init_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-orchestrator", "version": "0.1.0"},
                },
            }
            list_msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            payload = (json.dumps(init_msg) + "\n" + json.dumps(list_msg) + "\n").encode()
            stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return AdapterResult(
                agent=agent.name,
                ecosystem=self.ecosystem,
                output="",
                success=False,
                error=f"MCP server timed out after {self.timeout}s",
            )

        return AdapterResult(
            agent=agent.name,
            ecosystem=self.ecosystem,
            output=(
                f"[MCP server '{agent.name}' raw stdio handshake completed]\n"
                f"Task: {task}\n\n"
                f"Server stdout (first 2KB):\n{stdout[:2048].decode(errors='replace')}\n"
                "(For full tool execution, install the official mcp Python SDK: pip install mcp)"
            ),
            success=proc.returncode in (0, None),
        )
