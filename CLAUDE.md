# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

A **planning + knowledge layer for AI coding agents** — one Python entry point
that takes any task, routes it to the best curated domain knowledge pack from
60+ specialists, and returns a structured **PlanCard** (steps, gotchas, success
criteria, recalled memory, past outcomes) that the host LLM executes with its
own tools.

v2 design note: forgent used to run agents itself via per-ecosystem adapters
with their own tool-use loops. That was a duplication of the host LLM's
capabilities dressed up as a "persona swap" — and a prompt swap is not a
specialist. v2 inverts it: the host Claude stays in the driver's seat and
forgent contributes decomposition, retrieved memory, curated checklists, and
an outcome feedback loop. No adapters, no in-process tool loops.

The unique angle: **nobody has unified curated specialist knowledge with
planning + outcome-aware memory for coding agents**. Each ecosystem ships
personas; forgent ships *plans that learn*.

## Architecture

```
src/forgent/
├── __init__.py
├── orchestrator.py          # thin facade: advise() -> PlanCard, record_outcome(), forge_agent()
├── cli.py                   # `forgent advise "..."` Typer CLI
├── mcp_server.py            # stdio MCP server exposing advise_task / report_outcome / ...
├── memory/
│   └── store.py             # SQLite + FTS5 memory, includes OUTCOME type for feedback loop
├── registry/
│   ├── loader.py            # parses curated .md files into AgentSpec objects
│   ├── forge.py             # AgentForge — synthesizes new knowledge packs on demand
│   ├── catalog.yaml         # the curated registry
│   └── agents/              # vendored knowledge pack bodies
├── router/
│   └── router.py            # LLM task classifier, factors in past OUTCOMEs
└── planner/
    └── planner.py           # Planner + PlanCard — the heart of v2

sources/                     # cloned upstream repos (read-only inputs to curation)
```

### The v2 flow

```
task
  -> router.route(task)                 # pick knowledge pack
  -> memory.context_for(task)           # recall prior plans/outcomes/decisions
  -> memory.recent_outcomes(agent)      # pull feedback for that pack
  -> planner.plan(task, decision, ...)  # LLM tool-use -> PlanCard
  -> PlanCard.to_markdown()             # returned from advise_task
  -> [host LLM executes with own tools]
  -> report_outcome(session, success, notes) # closes the loop
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
- The planner gets smarter over time — past plans, routing decisions, AND outcomes feed the next task.
- `MemoryType.OUTCOME` closes the feedback loop: `record_outcome(session, success, notes, agent)` after a task is done, and the next plan for the same domain surfaces that history as gotchas.
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

## The planner and PlanCard

`src/forgent/planner/planner.py` is the centerpiece of v2. It takes a task
plus the routed knowledge pack body and produces a structured `PlanCard`:

```python
@dataclass
class PlanCard:
    task: str
    session_id: str
    primary_agent: str                 # knowledge pack name
    supporting: list[str]
    confidence: float
    routing_reasoning: str
    knowledge_pack_summary: str        # 2-4 sentences, distilled for THIS task
    steps: list[str]                   # 3-6 concrete imperative steps
    gotchas: list[str]                 # specific failure modes
    success_criteria: list[str]        # verifiable "done" conditions
    recalled_memory: str               # from memory.context_for()
    past_outcomes: list[str]           # from memory.recent_outcomes(agent)
    forged: bool
    heuristic: bool                    # true when no API key (deterministic fallback)
```

`PlanCard.to_markdown()` is the string returned by `advise_task`. It opens
with the assignment block (which the host is instructed to echo to the user),
then the host instructions, knowledge synthesis, steps, gotchas, success
criteria, past outcomes, and recalled memory.

**The planner is NOT writing a persona.** It's compiling domain knowledge
into a task-specific plan. "You are X" framing is banned; "here's what done
looks like, here are the gotchas, here's what prior sessions remembered"
framing is required.

## Conventions

- **Python ≥ 3.10**, type hints required on public APIs.
- **No emojis** in code, comments, or markdown (CLI output is fine to format with `rich`).
- **Don't import from `sources/`** at runtime. Treat it as a build-time input only.
- **Don't bypass `MemoryStore`** — it is the single source of truth for state.
- **Don't resurrect the adapter layer.** If you feel the urge to "run the agent"
  inside forgent, stop. The host LLM runs it. Forgent plans.
- **Tests live in `tests/`**, mirror the `src/` layout, prefer `pytest`.

## Adding a new agent to the registry

1. Find a strong candidate in `sources/` or upstream.
2. Add an entry to `src/forgent/registry/catalog.yaml`.
3. Run `python -m forgent.registry.loader --vendor` to copy the file in.
4. Run the smoke test: `forgent advise "test task that should match this agent"`.

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
.venv/bin/forgent advise "summarize the differences between LangGraph and CrewAI"
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
