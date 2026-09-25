#!/usr/bin/env bash
# Bootstrap forgent, then hand off to the guided setup.
#
#   ./scripts/install.sh                 # install from PyPI, then `forgent setup`
#   ./scripts/install.sh --local         # install this checkout (editable wheel build)
#   ./scripts/install.sh -- --channel mcp --scope project --yes
#                                        # everything after `--` goes to `forgent setup`
#
# Safe to re-run: it upgrades forgent in place and setup is idempotent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPEC="forgent"
if [[ "${1:-}" == "--local" ]]; then
    SPEC="$REPO_ROOT"
    shift
fi
[[ "${1:-}" == "--" ]] && shift

if command -v uv >/dev/null 2>&1; then
    echo "==> Installing forgent with uv"
    uv tool install --force --upgrade "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
    echo "==> Installing forgent with pipx"
    pipx install --force "$SPEC"
else
    echo "Neither uv nor pipx found. Install one of them first:"
    echo "  uv:   https://docs.astral.sh/uv/getting-started/installation/"
    echo "  pipx: https://pipx.pypa.io/stable/installation/"
    exit 1
fi

# macOS: files created inside Claude Code's sandbox can carry UF_HIDDEN, which
# makes Python's site.py skip .pth files. Clear it on the tool environments.
if [[ "$(uname)" == "Darwin" ]]; then
    for d in "$HOME/.local/share/uv/tools/forgent" "$HOME/.local/pipx/venvs/forgent"; do
        [[ -d "$d" ]] && find "$d" -name '*.pth' -exec chflags nohidden {} \; 2>/dev/null || true
    done
fi

export PATH="$HOME/.local/bin:$PATH"
exec forgent setup "$@"
