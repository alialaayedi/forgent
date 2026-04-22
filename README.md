<p align="center">
  <img src="https://raw.githubusercontent.com/alialaayedi/forgent/main/assets/brand/banner.svg" alt="forgent — a planning + knowledge layer for AI coding agents" width="100%"/>
</p>

# forgent

> **Plans that learn.** A planning + knowledge layer for AI coding agents. Give it a task; it routes to the best curated specialist out of 60+ knowledge packs, pulls relevant past outcomes from memory, and returns a structured **PlanCard** — steps, gotchas, success criteria, and a memory index — for your host LLM to execute with its own tools.
>
> Ships as a single stdio MCP server. Drop it into Claude Code, Claude Desktop, Cursor, Zed, or any MCP client; every session gets the same `advise_task` / `report_outcome` / `memory_view` surface.

<p align="center">
  <a href="https://pypi.org/project/forgent/"><img src="https://img.shields.io/pypi/v/forgent?style=flat-square&color=eb5160&labelColor=071013" alt="PyPI"/></a>
  <a href="https://github.com/alialaayedi/forgent/blob/main/docs/brand.md"><img src="https://img.shields.io/badge/palette-ink_•_lobster_•_taupe_•_silver_•_alabaster-eb5160?style=flat-square&labelColor=071013" alt="brand palette"/></a>
  <a href="https://github.com/alialaayedi/forgent/blob/main/docs/INTEGRATION.md"><img src="https://img.shields.io/badge/MCP-ready-eb5160?style=flat-square&labelColor=071013" alt="MCP ready"/></a>
  <img src="https://img.shields.io/badge/python-3.10+-b7999c?style=flat-square&labelColor=071013" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/license-MIT-aaaaaa?style=flat-square&labelColor=071013" alt="MIT"/>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/alialaayedi/forgent/main/assets/brand/demo.gif" alt="forgent in action — advise, recall, forge, outcome" width="100%"/>
</p>

## Why this exists

The agent ecosystem is fragmented into silos that don't talk to each other:

| Silo | Top repos | Strengths | Weaknesses |
|---|---|---|---|
| Claude Code subagents | wshobson/agents (32.7k★), VoltAgent/awesome-claude-code-subagents, 0xfurai/claude-code-subagents | huge variety of specialists, markdown-portable | only run inside Claude Code, no shared memory |
| Python frameworks | LangGraph, CrewAI, AutoGen, lastmile-ai/mcp-agent | production-ready workflows, eval tooling | need code, framework lock-in |
| MCP servers | modelcontextprotocol/servers, github/github-mcp-server | standardized tools and data access | one-server-per-tool, no orchestration |

Each ecosystem ships *personas*. A prompt swap isn't a specialist — and nobody ties that curated knowledge to a planning layer with an outcome-aware memory. Forgent does.

### Why a planning layer, not another agent runner

v1 of forgent ran agents itself via per-ecosystem adapters, each with its own tool-use loop. That duplicated the host LLM's capabilities while pretending a prompt swap was a specialist. **v2 inverts it:** the host Claude stays in the driver's seat with its own tools. Forgent contributes the things a single agent can't do on its own — task decomposition, curated checklists, retrieved memory across sessions, and an outcome feedback loop. No adapters, no in-process tool loops.

## What's inside

- **63 hand-curated knowledge packs** across 11 categories (core dev, language specialists, infrastructure, quality/security, data/AI, dev experience, specialized domains, business/product, meta-orchestration, research) — picked from the highest-quality public repos for definition quality, not auto-imported.
- **Planner + PlanCard** — the heart of v2. Compiles a routed knowledge pack plus past outcomes into 3–6 concrete steps, specific gotchas, verifiable success criteria, and a compact memory index. `PlanCard.to_markdown()` is what the host consumes.
- **LLM-based router** that maps any task → primary pack + supporting packs + confidence + reasoning, factoring in prior `OUTCOME` entries for that pack. Falls back to a deterministic heuristic when no API key is set.
- **SQLite + FTS5 memory** with an `OUTCOME` type that closes the feedback loop. `record_outcome(session, success, notes, agent)` after a task; the next plan for the same domain surfaces that history as gotchas. Zero external dependencies.
- **Virtual-path memory surface** (v0.3, mirrors Anthropic's `memory_20250818` protocol). The PlanCard carries paths like `/outcomes/<agent>/`, `/notes/<topic>/`, `/sessions/<sid>/`; the host pulls only what it needs via `memory_view(path)` and leaves breadcrumbs via `memory_write`.
- **AgentForge** — synthesizes brand-new knowledge packs on demand using Claude when no curated pack fits. Forged packs are persisted to `dynamic.yaml` + `registry/agents/claude_code/<name>.md` and appear in every future routing call.
- **Stdio MCP server** — the 12 tools below, so every Claude environment calls the same planning surface.
- **Typer-based CLI** for advising on tasks, recording outcomes, browsing the registry, forging packs, and inspecting memory.

### MCP tools exposed

| Tool | Purpose |
|---|---|
| `advise_task` | Route + plan; returns a PlanCard markdown for the host to execute |
| `revise_plan` | Amend a PlanCard with mid-flight findings |
| `report_outcome` | Close the loop — persist success/notes for a session |
| `memory_view` | Pull-based recall over virtual paths (`/outcomes/…`, `/notes/…`, `/sessions/…`, `/agents/…`) |
| `memory_write` | Write a breadcrumb note back to memory |
| `list_agents` | Registry listing, filterable by ecosystem/category |
| `search_agents` | Keyword search over the registry |
| `show_agent` | Full knowledge pack body |
| `recall_memory` | Ad-hoc FTS5 recall, optionally filtered by memory type |
| `memory_stats` | What's stored and how much |
| `forge_agent` | Synthesize a brand-new pack when none fits |
| `route_only` | Just the routing decision, no plan |

## Architecture

```
task
  -> router.route(task)                 # pick knowledge pack + supporting
  -> memory.context_for(task)           # short recall preview
  -> memory.recent_outcomes(agent)      # feedback for that pack
  -> orch._build_memory_index(agent)    # virtual paths, not a dumped blob
  -> planner.plan(...)                  # LLM tool-use -> PlanCard
  -> PlanCard.to_markdown()             # returned from advise_task
       ↓
  [host LLM executes with its own tools]
       ↓
  memory_view(path)                     # pulled on demand
  memory_write("/notes/<topic>", ...)   # host breadcrumbs
  report_outcome(session, success)      # closes the loop
```

Recall is **pull-based**. The PlanCard no longer dumps a big `recalled_memory` string — it carries a compact index and the host fetches only what it needs, mirroring the Anthropic `memory_20250818` tool shape.

## Install

Requires Python 3.10+.

### From PyPI (recommended)

```bash
pip install forgent              # core CLI + MCP server + status line
pip install "forgent[all]"       # + optional integrations
```

This puts `forgent`, `forgent-mcp`, and `forgent-statusline` on your `$PATH`.

Prefer an isolated install? [`pipx`](https://pipx.pypa.io) is the cleanest path:

```bash
pipx install forgent
```

### From source (development)

```bash
git clone https://github.com/alialaayedi/forgent.git
cd forgent
make install                          # creates .venv, installs editable, fixes macOS .pth quirk
make vendor                           # copies source agent files into the registry
make test                             # runs the smoke suite

cp .env.example .env                  # add ANTHROPIC_API_KEY
.venv/bin/forgent advise "hello"
```

### Register with every Claude environment

See **[docs/INTEGRATION.md](docs/INTEGRATION.md)** for the full guide. Short version:

```bash
# Claude Code (any project on your machine)
claude mcp add forgent \
  --env ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  --env FORGENT_DB=./forgent.db \
  -- $(which forgent-mcp)
```

For Claude Desktop, edit `~/Library/Application Support/Claude/claude_desktop_config.json` and add the server under `mcpServers` (snippet in the integration guide).

## Usage

### Ask for a plan

```bash
forgent advise "design a Stripe webhook handler with idempotency and PCI-safe logging"
```

The CLI will:
1. Route the task to the best curated pack (showing confidence + reasoning).
2. Pull recent `OUTCOME` entries for that pack.
3. Compile a PlanCard with steps, gotchas, success criteria, and a memory index.
4. Print the markdown the host LLM should execute.

### Close the loop

After executing the plan, tell forgent how it went:

```bash
forgent outcome <session-id> --success --notes "shipped; idempotency key lived in Redis"
```

The next plan for the same pack will surface this in `past_outcomes`.

### Browse the registry

```bash
forgent agents list                          # all 63 curated packs
forgent agents list --category data-ai       # filter by category
forgent agents list --ecosystem mcp          # filter by ecosystem
forgent agents search "kubernetes security"  # keyword search
forgent agents show backend-developer        # full knowledge pack body
```

### Inspect memory

```bash
forgent stats                          # overview
forgent memory stats                   # what's stored
forgent memory recall "stripe"         # what the planner would pull back
forgent memory recall "auth" --type routing
forgent memory forget                  # wipe (with confirmation)
```

### Forge new knowledge packs on demand

When no curated pack fits, grow one:

```bash
forgent forge "write Solidity smart contracts with formal verification (Certora, Halmos)"
```

Or in any Claude environment with the MCP server registered:

> "Use forge_agent to create a specialist for SAML 2.0 SSO integrations with Okta and Azure AD."

The new pack gets a structured body, capability tags, and is persisted to `dynamic.yaml` + `registry/agents/claude_code/<name>.md`. From then on every `list_agents`, `search_agents`, and `route_only` call sees it.

### Vendor agent files for offline use

```bash
forgent vendor          # copies source .md files into the registry
forgent vendor --force  # overwrite existing vendored files
```

After vendoring, `sources/` can be deleted — the registry is self-contained.

## Memory system

`src/forgent/memory/store.py` is a SQLite database with an FTS5 virtual table for full-text recall. Every interaction lands in there.

| Memory type | What it is |
|---|---|
| `task` | the original user request |
| `routing` | the router's decision and reasoning |
| `plan` | PlanCards the planner produced |
| `outcome` | post-execution success/failure + notes (v0.3 feedback loop) |
| `agent_output` | what a host agent produced (when the host writes it back) |
| `agent_doc` | curated pack definitions, for retrieval-aware routing |
| `note` | free-form breadcrumbs from the host or user |
| `artifact` | file paths or blobs |

v0.3 adds a **virtual path layer** over the same tables — paths are derived from `(type, source, tags)`, no schema change:

| Path | Maps to |
|---|---|
| `/outcomes/<agent>/` | `OUTCOME` entries where `source=<agent>` |
| `/plans/<agent>/` | `PLAN` entries where `source=<agent>` |
| `/notes/<topic>/` | `NOTE` entries tagged `host-note` + `<topic>` |
| `/sessions/<sid>/` | all entries for session `<sid>` |
| `/agents/<name>` | the curated pack body |

Before every plan, forgent composes a small memory index into the PlanCard and the host pulls paths on demand through `memory_view`. Past routing + outcomes become few-shot context for the next task.

## Adding packs to the registry

1. Find a strong candidate in `sources/` or any GitHub repo.
2. Add an entry to `src/forgent/registry/catalog.yaml` with `name`, `ecosystem`, `category`, `capabilities`, `source_repo`, `source_path`, `description`.
3. Run `forgent vendor` to copy the body into `src/forgent/registry/agents/`.
4. Smoke test: `forgent advise "task that should match this pack"`.

## Source repos used for curation

| Repo | Stars | What was taken |
|---|---|---|
| [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) | high | ~45 packs across 10 categories — primary source |
| [wshobson/agents](https://github.com/wshobson/agents) | 32.7k★ | plugin-style packs and orchestration patterns |
| [0xfurai/claude-code-subagents](https://github.com/0xfurai/claude-code-subagents) | high | language/framework experts (138 single-file packs) |
| [lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent) | growing | workflow patterns (router, orchestrator, evaluator-optimizer, swarm) |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | official | filesystem, fetch, reference MCP servers |
| [github/github-mcp-server](https://github.com/github/github-mcp-server) | official | GitHub MCP server |

## Contributing

MIT-licensed and free to use forever. Contributions of any size are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the quickstart (`make install && make test`).

High-value ideas:
- Vector embedding column alongside FTS5 for hybrid retrieval.
- A web dashboard for browsing sessions, plans, and forged packs.
- `forge_from_examples` — synthesize a pack from a few input/output pairs.
- Weekly upstream sync that refreshes the catalog from source repos.

### Funding model — coming in v0.2

Forgent is experimenting with a contributor-reward model: a portion of donations pooled and shared with contributors who land merged PRs, distributed transparently via [Open Collective](https://opencollective.com). Accounts go live in v0.2; prior contributors will be retroactively credited.

## License

MIT. Curated pack definitions retain their original licenses from the source repos.
