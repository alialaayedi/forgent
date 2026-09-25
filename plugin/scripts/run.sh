#!/usr/bin/env sh
# Launcher used by the forgent Claude Code plugin (MCP server + hooks).
#
#   run.sh forgent-mcp            start the MCP server
#   run.sh forgent hook <event>   run a hook handler
#
# Resolution order: an installed binary on PATH (pipx/pip), then uvx, then
# pipx run. Set FORGENT_SPEC to pin the package, e.g. "forgent==0.5.0".
set -eu

bin="${1:?usage: run.sh <forgent|forgent-mcp> [args...]}"
shift
spec="${FORGENT_SPEC:-forgent>=0.5.0}"

if command -v "$bin" >/dev/null 2>&1; then
  exec "$bin" "$@"
elif command -v uvx >/dev/null 2>&1; then
  exec uvx --quiet --from "$spec" "$bin" "$@"
elif command -v pipx >/dev/null 2>&1; then
  exec pipx run --spec "$spec" "$bin" "$@"
fi

# Hooks must never break a session: exit quietly when forgent is missing.
if [ "$bin" = "forgent" ]; then
  exit 0
fi
echo "forgent: '$bin' not found. Install with 'pipx install forgent' or install uv (https://docs.astral.sh/uv/)." >&2
exit 127
