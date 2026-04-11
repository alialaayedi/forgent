# forgent examples

Runnable scripts that show how to use forgent as a Python library. Each one is
self-contained — copy any of them into your own project as a starting point.

| File | What it shows | Needs API key |
|---|---|---|
| [`01_quickstart.py`](01_quickstart.py) | Minimal end-to-end: route → plan → PlanCard | no (heuristic) |
| [`02_forge_specialist.py`](02_forge_specialist.py) | Forge a brand-new knowledge pack on demand, then plan against it | yes |
| [`03_memory_recall.py`](03_memory_recall.py) | Persist past sessions and recall relevant context for new tasks | no |

## Setup

```bash
pip install forgent

# For examples that call the LLM planner:
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```bash
python examples/01_quickstart.py
python examples/02_forge_specialist.py
python examples/03_memory_recall.py
```

## What's missing?

These examples deliberately don't cover the MCP server side — for that, the
right path is to register `forgent-mcp` with Claude Code or Claude Desktop and
call `advise_task` / `report_outcome` from inside an actual session. See
[`docs/INTEGRATION.md`](../docs/INTEGRATION.md).
