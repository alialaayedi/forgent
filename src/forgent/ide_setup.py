"""forgent setup-ide <editor>: generate MCP client config snippets.

forgent is MCP-native. Any MCP-aware host (Claude Code, Cursor, Cline, Zed
Editor, Continue) can point at the same `forgent-mcp` binary. This module
prints per-editor install instructions + the exact JSON/TOML they need.

No runtime behavior change -- this is pure developer ergonomics. Addresses
competitive-gap #7 (first-class IDE surfaces beyond Claude Code) from the
v0.4 analysis: generic docs cost nothing but make forgent the first planning
layer that's documented to work with every major MCP host.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class IDESnippet:
    editor: str
    config_path: str       # where to drop the config, relative or absolute
    format: str            # "json" | "toml" | "env"
    snippet: str           # the exact text to paste
    notes: str = ""        # any follow-up instructions


def forgent_mcp_path() -> str:
    """Best-effort absolute path to the forgent-mcp binary.

    Most installs put it at ~/.local/bin/forgent-mcp (pipx) or inside the
    venv's bin/. Falls back to the bare command name for $PATH lookup.
    """
    p = shutil.which("forgent-mcp")
    return p or "forgent-mcp"


def snippet_for(editor: str) -> IDESnippet:
    """Return the install snippet for a supported editor."""
    normalized = editor.strip().lower()
    if normalized not in SUPPORTED:
        raise ValueError(
            f"unknown editor {editor!r}. Supported: {', '.join(SUPPORTED.keys())}"
        )
    return SUPPORTED[normalized]()


def _claude_code_desktop() -> IDESnippet:
    config_path = str(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    body = {
        "mcpServers": {
            "forgent": {
                "command": forgent_mcp_path(),
                "env": {
                    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                    "FORGENT_DB": "./forgent.db",
                },
            }
        }
    }
    return IDESnippet(
        editor="Claude Desktop",
        config_path=config_path,
        format="json",
        snippet=json.dumps(body, indent=2),
        notes=(
            "Merge this into your existing claude_desktop_config.json. "
            "Restart Claude Desktop after saving."
        ),
    )


def _claude_code_cli() -> IDESnippet:
    cmd = (
        f'claude mcp add --scope user forgent --env ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" '
        f'-- {forgent_mcp_path()}'
    )
    return IDESnippet(
        editor="Claude Code (CLI)",
        config_path="managed via `claude mcp add`",
        format="env",
        snippet=cmd,
        notes="Run once. Close and reopen any Claude Code windows to pick it up.",
    )


def _cursor() -> IDESnippet:
    config_path = str(Path.home() / ".cursor" / "mcp.json")
    body = {
        "mcpServers": {
            "forgent": {
                "command": forgent_mcp_path(),
                "env": {
                    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                    "FORGENT_DB": "./forgent.db",
                },
            }
        }
    }
    return IDESnippet(
        editor="Cursor",
        config_path=config_path,
        format="json",
        snippet=json.dumps(body, indent=2),
        notes=(
            "Drop this at ~/.cursor/mcp.json. Cursor auto-detects on restart. "
            "Requires Cursor 0.45+."
        ),
    )


def _cline() -> IDESnippet:
    body = {
        "mcpServers": {
            "forgent": {
                "command": forgent_mcp_path(),
                "env": {
                    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                    "FORGENT_DB": "./forgent.db",
                },
            }
        }
    }
    return IDESnippet(
        editor="Cline (VS Code)",
        config_path=(
            "In VS Code: Cline panel -> MCP Servers (wrench icon) -> Edit Config"
        ),
        format="json",
        snippet=json.dumps(body, indent=2),
        notes=(
            "Paste into Cline's MCP config JSON. Works identically for Roo Code "
            "since it shares Cline's MCP format."
        ),
    )


def _zed() -> IDESnippet:
    body = {
        "context_servers": {
            "forgent": {
                "command": {
                    "path": forgent_mcp_path(),
                    "args": [],
                    "env": {
                        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                        "FORGENT_DB": "./forgent.db",
                    },
                }
            }
        }
    }
    return IDESnippet(
        editor="Zed Editor",
        config_path="In Zed: Cmd-K Cmd-S -> open settings.json",
        format="json",
        snippet=json.dumps(body, indent=2),
        notes=(
            "Zed calls MCP servers 'context_servers'. Merge the block above "
            "into your settings.json. Requires Zed 0.170+."
        ),
    )


def _continue() -> IDESnippet:
    config_path = str(Path.home() / ".continue" / "config.yaml")
    snippet = (
        "experimental:\n"
        "  modelContextProtocolServers:\n"
        "    - transport:\n"
        "        type: stdio\n"
        f"        command: {forgent_mcp_path()}\n"
        "        args: []\n"
        "        env:\n"
        "          ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}\n"
        "          FORGENT_DB: ./forgent.db\n"
    )
    return IDESnippet(
        editor="Continue",
        config_path=config_path,
        format="toml",  # YAML really, but we only care that it's non-json
        snippet=snippet,
        notes=(
            "Append under `experimental.modelContextProtocolServers`. "
            "Requires Continue 0.9.220+. Restart the extension after saving."
        ),
    )


# Dispatch table. Keep names short + lowercase; CLI accepts aliases.
SUPPORTED = {
    "claude-code": _claude_code_cli,
    "claude-desktop": _claude_code_desktop,
    "cursor": _cursor,
    "cline": _cline,
    "roo": _cline,
    "roo-code": _cline,
    "zed": _zed,
    "continue": _continue,
}
