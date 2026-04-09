# forgent examples

Runnable scripts that show how to use forgent as a Python library. Each one is
self-contained — copy any of them into your own project as a starting point.

| File | What it shows | Needs API key |
|---|---|---|
| [`01_quickstart.py`](01_quickstart.py) | Minimal end-to-end: route → dispatch → result | yes |
| [`02_forge_specialist.py`](02_forge_specialist.py) | Forge a brand-new specialist subagent on demand, then use it | yes |
| [`03_memory_recall.py`](03_memory_recall.py) | Persist past sessions and recall relevant context for new tasks | no |
| [`04_custom_adapter.py`](04_custom_adapter.py) | Implement a new ecosystem adapter (the smallest possible) | no |

## Setup

```bash
pip install forgent

# For examples that need a key:
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
python examples/01_quickstart.py
python examples/02_forge_specialist.py
python examples/03_memory_recall.py
python examples/04_custom_adapter.py
```

## What's missing?

These examples deliberately don't cover the MCP server side — for that, the
right path is to register `forgent-mcp` with Claude Code or Claude Desktop and
use the orchestrator from inside an actual Claude session. See
[`docs/INTEGRATION.md`](../docs/INTEGRATION.md).
