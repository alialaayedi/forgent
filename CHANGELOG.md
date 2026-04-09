# Changelog

All notable changes to forgent are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-04-09

The first public release. Everything below is new.

### Added

- **63 hand-curated specialist agents** across 11 categories, picked from the
  highest-quality public repos:
  - [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) — primary source
  - [wshobson/agents](https://github.com/wshobson/agents) (32.7k stars)
  - [0xfurai/claude-code-subagents](https://github.com/0xfurai/claude-code-subagents)
  - [lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent) — workflow patterns
  - Reference MCP servers from [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)
- **AgentForge** (`forgent.registry.forge.AgentForge`) — synthesizes brand-new
  specialist subagents on demand via Claude tool-use. Forged agents are
  persisted to `dynamic.yaml` + `agents/claude_code/<name>.md` and become
  available immediately to every future call.
- **LLM-based task router** (`forgent.router.router.Router`) using Anthropic
  structured tool-use to pick the best primary agent + supporting agents +
  execution mode (single / sequential / parallel / evaluator-optimizer).
  Falls back to keyword scoring when no API key is available.
- **SQLite + FTS5 memory store** (`forgent.memory.MemoryStore`) — persists
  every task, routing decision, agent output, decision, and forged-agent
  doc. Recalls relevant past context for each new task via BM25 ranking.
  Zero external dependencies.
- **Three async ecosystem adapters** with a common `Adapter` ABC:
  - `ClaudeCodeAdapter` — executes markdown subagents via the Anthropic API
  - `PythonFrameworkAdapter` — native implementations of router, orchestrator,
    parallel, evaluator-optimizer, swarm, and deep-orchestrator workflow
    patterns from `lastmile-ai/mcp-agent`
  - `MCPAdapter` — spawns MCP servers via stdio with optional `mcp` Python SDK
- **Stdio MCP server** (`forgent-mcp`) exposing 8 tools to any MCP client:
  `run_task`, `forge_agent`, `list_agents`, `search_agents`, `show_agent`,
  `recall_memory`, `memory_stats`, `route_only`. Drops into Claude Code,
  Claude Desktop, Cursor, Zed, and any other MCP-compatible client.
- **Typer CLI** (`forgent`) with brand-themed `rich` output:
  `forgent run`, `forgent agents list/search/show`, `forgent memory
  stats/recall/forget`, `forgent vendor`, `forgent forge`, `forgent stats`.
- **Auto-forge mode** — `forgent run --auto-forge "..."` synthesizes a fresh
  specialist for the task class when the router's confidence is below 0.4.
- **Per-project memory** — the `FORGENT_DB` environment variable controls
  the SQLite path; default `./forgent.db` makes each working directory its
  own knowledge base.
- **Brand identity**: ink-black + lobster-pink + rosy-taupe palette with
  canonical assets in `assets/brand/` (banner, icon, icon-mark, horizontal
  lockup, social preview, VHS demo tape) and a brand guide at
  `docs/brand.md`.
- **Shippable wheel** (`forgent-0.1.0-py3-none-any.whl`, 200 KB) with all
  vendored agent definitions included.
- **Documentation**:
  - [`README.md`](README.md) — overview, install, usage, contributor model
  - [`CLAUDE.md`](CLAUDE.md) — guidance for Claude Code working in the repo
  - [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to add agents and adapters
  - [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — Claude Code / Desktop / MCP setup
  - [`docs/brand.md`](docs/brand.md) — brand guide
  - [`docs/github-repo-setup.md`](docs/github-repo-setup.md) — launch cheat sheet
  - [`examples/`](examples/) — 4 runnable Python examples
- **CI**: GitHub Actions runs the smoke suite on Python 3.10–3.13 and builds
  the wheel on every push.
- **One-shot installer** (`scripts/install.sh`) — handles pipx, the macOS
  `UF_HIDDEN` editable-install quirk, and prints registration commands for
  every Claude environment.

### Known limitations

- Memory recall is keyword-based (FTS5 BM25). Vector embedding column is on
  the v0.2 roadmap.
- The Python framework adapter ships native implementations of the workflow
  patterns; the optional `[langgraph]`, `[crewai]`, `[mcp]` extras install
  the real frameworks but no adapters are wired in for them yet.
- Forged agents are stored inside the package directory and replaced on
  reinstall. To keep them safe across upgrades, copy `dynamic.yaml` to a
  backup. The v0.2 plan adds `FORGENT_DYNAMIC_DIR` for an external path.
- The CLI confirms destructive operations (`forgent memory forget`) but
  there is no undo for forged agents — once persisted, they're persisted.

[Unreleased]: https://github.com/alialaayedi/forgent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alialaayedi/forgent/releases/tag/v0.1.0
