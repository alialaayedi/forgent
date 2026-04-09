#!/usr/bin/env bash
# One-shot installer for the agent orchestrator.
#
# What it does:
#   1. Installs `pipx` if missing (macOS via brew, Linux via apt/dnf if available)
#   2. Installs the orchestrator from the local wheel via pipx (isolated venv,
#      globally-callable `orchestrator` and `orchestrator-mcp` binaries)
#   3. Clears the macOS UF_HIDDEN flag on any pipx-managed .pth files
#   4. Prints the exact commands to register the MCP server with Claude Code
#      and Claude Desktop
#
# Usage:
#   ./scripts/install.sh                        # install from dist/*.whl in this repo
#   ./scripts/install.sh path/to/agent_orchestrator.whl
#
# Re-running is safe — pipx handles upgrades.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHEEL="${1:-}"

if [[ -z "$WHEEL" ]]; then
    WHEEL="$(ls -t "$REPO_ROOT"/dist/agent_orchestrator-*.whl 2>/dev/null | head -1 || true)"
fi

if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
    echo "No wheel found. Build one first:"
    echo "  cd $REPO_ROOT && python3 -m build --wheel"
    exit 1
fi

echo "==> Installing from $WHEEL"

# 1. Ensure pipx is available
if ! command -v pipx >/dev/null 2>&1; then
    echo "==> pipx not found, installing"
    if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
        brew install pipx
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y pipx
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y pipx
    else
        python3 -m pip install --user pipx
    fi
    pipx ensurepath
    export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Install (or upgrade) the orchestrator into an isolated venv
pipx install --force "$WHEEL"

# 3. Clear UF_HIDDEN on any .pth files (macOS sandbox quirk)
if [[ "$(uname)" == "Darwin" ]]; then
    PIPX_VENV="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || echo "$HOME/.local/pipx/venvs")/agent-orchestrator"
    if [[ -d "$PIPX_VENV" ]]; then
        find "$PIPX_VENV" -name '*.pth' -exec chflags nohidden {} \; 2>/dev/null || true
    fi
fi

# 4. Locate the orchestrator-mcp binary and print registration commands
ORCH_BIN="$(command -v orchestrator || echo "$HOME/.local/bin/orchestrator")"
MCP_BIN="$(command -v orchestrator-mcp || echo "$HOME/.local/bin/orchestrator-mcp")"

cat <<EOF

==> Installed.

  orchestrator       -> $ORCH_BIN
  orchestrator-mcp   -> $MCP_BIN

Try it:

  orchestrator stats
  orchestrator agents search "kubernetes"
  orchestrator run "your task here"
  orchestrator forge "design RFC-compliant SAML 2.0 SSO integrations"

----------------------------------------------------------------------
Register the MCP server with Claude Code (any project, any directory):

  claude mcp add agent-orchestrator -- $MCP_BIN

  # With per-project memory (run this from the project dir you want to use):
  claude mcp add agent-orchestrator \\
    --env ANTHROPIC_API_KEY=\$ANTHROPIC_API_KEY \\
    --env ORCHESTRATOR_DB=./orchestrator.db \\
    -- $MCP_BIN

----------------------------------------------------------------------
Register the MCP server with Claude Desktop:

  Edit ~/Library/Application\\ Support/Claude/claude_desktop_config.json
  (macOS) or %APPDATA%\\Claude\\claude_desktop_config.json (Windows) and add:

  {
    "mcpServers": {
      "agent-orchestrator": {
        "command": "$MCP_BIN",
        "env": {
          "ANTHROPIC_API_KEY": "sk-ant-...",
          "ORCHESTRATOR_DB": "/Users/$(whoami)/.orchestrator.db"
        }
      }
    }
  }

  Then restart Claude Desktop. The orchestrator's tools (run_task,
  list_agents, search_agents, show_agent, recall_memory, memory_stats,
  forge_agent, route_only) will appear in Claude's tool list.
----------------------------------------------------------------------
EOF
