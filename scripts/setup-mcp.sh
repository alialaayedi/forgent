#!/usr/bin/env bash
# scripts/setup-mcp.sh — one-command forgent MCP setup
#
# Persists ANTHROPIC_API_KEY into your shell rc, registers forgent with
# Claude Code at user scope, and tells you exactly what to do next.
#
# Usage:
#   ./scripts/setup-mcp.sh                # interactive — prompts for the key
#   ANTHROPIC_API_KEY=sk-ant-... ./scripts/setup-mcp.sh   # if already in env
#
# Re-running is safe — it removes any existing forgent registration first.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORGENT_BIN="$REPO_ROOT/.venv/bin/forgent-mcp"
FORGENT_SRC="$REPO_ROOT/src"

# ----- 0. preflight ---------------------------------------------------------

if [[ ! -x "$FORGENT_BIN" ]]; then
    echo "ERROR: $FORGENT_BIN not found or not executable."
    echo "Run \`make install\` first to set up the venv."
    exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: 'claude' CLI not on PATH. Install Claude Code first."
    exit 1
fi

# ----- 1. get the API key ---------------------------------------------------

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "==> ANTHROPIC_API_KEY not set in this shell."
    echo "    Get one at: https://console.anthropic.com/settings/keys"
    echo
    printf "    Paste your key (input is hidden, press Enter when done): "
    read -rs ANTHROPIC_API_KEY
    echo
    if [[ -z "$ANTHROPIC_API_KEY" ]]; then
        echo "ERROR: empty key. Aborting."
        exit 1
    fi
    if ! [[ "$ANTHROPIC_API_KEY" =~ ^sk-ant- ]]; then
        echo "ERROR: key doesn't start with 'sk-ant-'. Are you sure?"
        exit 1
    fi
    export ANTHROPIC_API_KEY
fi

echo "==> Key loaded (length ${#ANTHROPIC_API_KEY})"

# ----- 2. persist to shell rc -----------------------------------------------

# Pick the right rc file for the user's shell
case "${SHELL##*/}" in
    zsh)  RC="$HOME/.zshrc" ;;
    bash) RC="$HOME/.bashrc" ;;
    fish) RC="$HOME/.config/fish/config.fish" ;;
    *)    RC="$HOME/.profile" ;;
esac

if grep -q "ANTHROPIC_API_KEY" "$RC" 2>/dev/null; then
    echo "==> $RC already references ANTHROPIC_API_KEY (leaving it alone)"
else
    echo "==> Appending export line to $RC"
    echo "" >> "$RC"
    echo "# Anthropic API key for forgent and other Anthropic SDK callers" >> "$RC"
    echo "export ANTHROPIC_API_KEY=\"$ANTHROPIC_API_KEY\"" >> "$RC"
    chmod 600 "$RC"
fi

# ----- 3. register forgent --------------------------------------------------

echo "==> Removing any existing forgent MCP registration"
claude mcp remove forgent 2>/dev/null || true

echo "==> Registering forgent at user scope"
claude mcp add --scope user forgent \
    --env ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    --env PYTHONPATH="$FORGENT_SRC" \
    --env FORGENT_DB="./.forgent.db" \
    -- "$FORGENT_BIN"

# ----- 4. verify ------------------------------------------------------------

echo
echo "==> Verifying"
if claude mcp list 2>&1 | grep -q "^forgent:.*Connected"; then
    echo "    forgent connected"
else
    echo "    forgent did NOT connect — check 'claude mcp list' for details"
    exit 1
fi

# ----- 5. final instructions ------------------------------------------------

cat <<'EOF'

────────────────────────────────────────────────────────────────────────────
forgent is now set up. Two important notes:

1. ALREADY-OPEN Claude Code windows have stale forgent subprocesses.
   For each Claude Code window where you want to use forgent:
   - Cmd+Q to quit Claude Code completely (not just close the tab)
   - Reopen the project
   - The forgent subprocess will respawn fresh with the new code + env

2. In a NEW Claude Code session, verify by asking:
       "Use forgent's route_only tool for: a typescript bug fix"
   You should see the new card layout with a confidence above 0.80 (LLM
   router, not the 0.50 heuristic fallback).
────────────────────────────────────────────────────────────────────────────
EOF
