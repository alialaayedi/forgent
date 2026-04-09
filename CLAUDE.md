# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

A **meta-orchestrator for AI agents** — one Python entry point that takes any task,
classifies it, picks the best curated agent from across three ecosystems
(Claude Code subagents, Python multi-agent frameworks, MCP servers), runs it,
and remembers everything for next time.

The unique angle: **nobody has unified these three ecosystems into one router**.
Each is a silo today. The product pitch is "Stripe for AI agents — one API,
route to whichever stack is best."

## Architecture

```
src/forgent/
├── __init__.py
├── orchestrator.py          # top-level Orchestrator class — the entry point
├── cli.py                   # `forgent run "..."` Typer CLI
├── memory/
│   ├── store.py             # SQLite + FTS5 knowledge base (the memory system)
│   └── __init__.py
├── registry/
│   ├── loader.py            # parses curated agent .md files into AgentSpec objects
│   ├── catalog.yaml         # the curated registry — picks the best agents from sources/
│   └── agents/              # vendored copies of curated agent files
├── router/
│   └── router.py            # LLM-based task → agent classifier
└── adapters/
    ├── base.py              # Adapter ABC
    ├── claude_code.py       # runs Claude Code subagent .md files via Anthropic API
    ├── python_framework.py  # wraps LangGraph / CrewAI / OpenAI Agents SDK
    └── mcp_server.py        # spawns MCP servers via stdio

sources/                     # cloned upstream repos (read-only inputs to curation)
├── wshobson-agents/         # 32.7k★ — 182 agents in plugin structure
├── voltagent-subagents/     # 100+ agents organized by category
├── furai-subagents/         # 138 single-file experts
└── lastmile-mcp-agent/      # MCP-based Python framework with workflow patterns
```

## The memory system (read this first)

`src/forgent/memory/store.py` is the brain. Every task creates a session,
every agent output is persisted, and the next task automatically pulls relevant
past context via FTS5 full-text search. **Always go through `MemoryStore`** —
do not store state in module-level globals or in adapter instances.

Key API:

```python
from forgent.memory import MemoryStore, MemoryType

mem = MemoryStore("./forgent.db")
sid = mem.start_session("Build a Stripe webhook handler")
mem.remember("Used backend-developer agent", MemoryType.ROUTING, session_id=sid)
mem.remember(agent_output, MemoryType.AGENT_OUTPUT, session_id=sid, tags=["stripe"])

# On the next task, this returns the relevant prior context as a single string
# ready to inject into the next agent's system prompt:
context = mem.context_for("add a refund endpoint to my Stripe integration")
```

Why this matters:
- The orchestrator gets smarter over time — past routing decisions inform future ones.
- Cross-ecosystem handoff works because every adapter writes to the same store.
- Recall is keyword (FTS5/BM25), not embedding — zero external dependencies, fast.
- An optional embedding column can be added later without changing the API.

**Never** add a new persistence layer alongside this. Extend `MemoryStore` instead.

## The agent registry

`src/forgent/registry/catalog.yaml` is hand-curated — agents are picked from
the cloned `sources/` for quality, not auto-imported. Each entry has:

```yaml
- name: backend-developer
  ecosystem: claude_code           # claude_code | python_framework | mcp
  category: core-development
  source_repo: voltagent-subagents
  source_path: categories/01-core-development/backend-developer.md
  capabilities: [api_design, microservices, scalability, security]
  description: Server-side APIs, microservices, production backends
  model: sonnet
```

The loader vendors a copy into `registry/agents/<ecosystem>/<name>.md` so the
project is self-contained — sources/ can be deleted after curation.

## Adapters

All adapters implement `forgent.adapters.base.Adapter`:

```python
class Adapter(ABC):
    ecosystem: str

    async def run(self, agent: AgentSpec, task: str, context: str) -> AdapterResult: ...
```

Keep adapters narrow. They translate from the orchestrator's neutral
`(agent, task, context)` shape to whatever the underlying ecosystem expects,
and translate the response back. Business logic and routing live above them.

## Conventions

- **Python ≥ 3.10**, type hints required on public APIs.
- **No emojis** in code, comments, or markdown (CLI output is fine to format with `rich`).
- **Async by default** in adapters — orchestrator runs them in `asyncio.gather` when fanning out.
- **Don't import from `sources/`** at runtime. Treat it as a build-time input only.
- **Don't bypass `MemoryStore`** — it is the single source of truth for state.
- **Tests live in `tests/`**, mirror the `src/` layout, prefer `pytest`.

## Adding a new agent to the registry

1. Find a strong candidate in `sources/` or upstream.
2. Add an entry to `src/forgent/registry/catalog.yaml`.
3. Run `python -m forgent.registry.loader --vendor` to copy the file in.
4. Run the smoke test: `forgent run "test task that should match this agent"`.

## Running

```bash
make install            # creates .venv, installs in editable mode, fixes the macOS .pth quirk
make vendor             # copies source agent files into the registry
make test               # runs the smoke suite
```

Or manually:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
# macOS only — clear the UF_HIDDEN flag so Python's site.py picks up the .pth file
chflags nohidden .venv/lib/python3.13/site-packages/__editable__.*.pth
cp .env.example .env    # add ANTHROPIC_API_KEY
.venv/bin/forgent run "summarize the differences between LangGraph and CrewAI"
```

### macOS sandbox quirk (important)

If `python -c "import forgent"` returns `ModuleNotFoundError` after a clean editable install, the `.pth` file in `.venv/lib/.../site-packages/` has the `UF_HIDDEN` macOS flag set (this happens inside Claude Code's sandbox). Python's `site.py` silently skips hidden `.pth` files. Fix:

```bash
chflags nohidden .venv/lib/python3.13/site-packages/__editable__.*.pth
```

The `Makefile` runs this automatically as part of `make install`.

## Source repos (read-only)

These are vendored under `sources/` for offline access during curation.
Re-clone with `make refresh-sources` if you want updates.

| Repo | Stars | What's good |
|---|---|---|
| wshobson/agents | 32.7k | 182 production agents organized as plugins |
| VoltAgent/awesome-claude-code-subagents | high | Cleanest category structure (10 categories) |
| 0xfurai/claude-code-subagents | high | 138 single-file language/framework experts |
| lastmile-ai/mcp-agent | growing | MCP-based workflow patterns (router, orchestrator, evaluator) |
