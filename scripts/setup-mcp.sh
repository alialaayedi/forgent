#!/usr/bin/env bash
# Deprecated: kept so old docs and muscle memory still work.
#
# The old version of this script wrote ANTHROPIC_API_KEY into your shell rc
# and into the MCP registration (~/.claude.json). `forgent setup` never
# stores secrets; export the key in your shell profile instead.
#
# If you ran the old script, re-register cleanly with:
#   forgent setup --channel mcp --replace
set -euo pipefail
echo "scripts/setup-mcp.sh is deprecated; running: forgent setup --channel mcp $*" >&2
exec forgent setup --channel mcp "$@"
