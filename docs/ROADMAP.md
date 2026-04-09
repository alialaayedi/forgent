# forgent — roadmap

This is the public direction document. It captures what's planned, what's been
filed as a known limitation, and what's *deliberately* out of scope. Things on
this list are not commitments — they're priorities.

If you want to work on something here, open a PR. If you want to propose
something not on this list, open an issue first so we can talk scope before
you write code.

## v0.1.1 — patch release (no breaking changes)

Triage-level fixes that don't need new design work. Cut whenever the list
fills up.

- [ ] **Better error message when `ANTHROPIC_API_KEY` is missing.** Right now
      the adapter returns a result with `error="ANTHROPIC_API_KEY not set or
      anthropic SDK not installed"` but the CLI/MCP surfaces don't always
      surface that prominently. The first thing a new user sees should be a
      clear "go to console.anthropic.com and set ANTHROPIC_API_KEY" instead
      of a silent half-success.
- [ ] **MCP tool descriptions should warn when LLM-backed tools are unusable.**
      `run_task` and `forge_agent` should advertise their dependency on the
      API key in their docstring so MCP clients can show it before invoking.
- [ ] **macOS sandbox `UF_HIDDEN` workaround.** The editable-install `.pth`
      file gets re-marked hidden by the macOS sandbox in some IDE contexts,
      breaking `import forgent`. The Makefile has `chflags nohidden` but
      pipx-installed users can hit this too. Consider falling back to a
      non-editable install path or a `forgent.pth` shipped at install time.
- [ ] **Heuristic router scoring is too keyword-bag-of-words.** A query like
      "review my Python code for security issues" matches `python-pro` first
      because "python" is in the capabilities, but should match `code-reviewer`
      or `security-auditor`. Bias the heuristic toward agents whose
      *category* matches the verb in the task ("review" → quality-security).
- [ ] **`forgent forge` should print the saved file path** so users can grep
      it later without poking around `dynamic.yaml`.

## v0.2.0 — next minor release

The big themes for v0.2 are: **stop requiring a raw API key**, **stop trying
to execute multi-step tasks single-shot**, and **let dynamic agents survive
upgrades**.

### Authentication

- **Support Claude Code OAuth credentials when `ANTHROPIC_API_KEY` is
  unset.** Right now forgent's `ClaudeCodeAdapter` instantiates
  `anthropic.Anthropic(api_key=...)` directly, which only knows about raw
  API keys. The Anthropic SDK doesn't yet support reusing Claude Code's
  OAuth tokens, but it ships an `AsyncAnthropic` that takes a custom auth
  callable. Plumb that through, with a fallback hierarchy:
  1. `ANTHROPIC_API_KEY` env var (current behavior)
  2. Claude Code OAuth via `~/.claude/credentials` if Claude Code is
     installed and authenticated
  3. AWS Bedrock / GCP Vertex creds if `AWS_REGION` / `GOOGLE_CLOUD_PROJECT`
     is set
  4. Fail with a clear error message linking to docs

  This removes the single biggest friction point for new users — getting
  forgent working without making them create an Anthropic Console account
  *separate* from their Claude Code login.

- **Per-tenant API key passthrough in MCP mode.** When forgent runs as an
  MCP server, the calling Claude session may want to pass its own API key
  through (so usage shows up on the right account). Add a `__credentials`
  parameter to MCP tools or honor an `X-Anthropic-Api-Key` header from the
  client.

### Task taxonomy

- **Detect when `run_task` should be a workflow, not a single dispatch.**
  Right now `run_task("audit my entire monorepo")` picks ONE specialist and
  dispatches ONE prompt — which obviously can't read 30 files. The router
  should classify the task into one of:
  - **single** (current behavior) — one specialist, one prompt
  - **workflow** — route through `workflow-orchestrator-pattern` or
    `workflow-deep-orchestrator` for multi-step decomposition
  - **handoff** — when the task needs filesystem/shell access, return a
    structured "this is a Claude Code task, here's the recommended specialist
    persona, you should run it directly" response instead of trying to
    execute server-side
- **`route_only` should also surface the recommended *execution mode***
  (single / workflow / handoff) so MCP clients can decide whether to run
  through forgent or take over themselves.

### Memory & dynamic agents

- **External `FORGENT_DYNAMIC_DIR`.** Forged agents currently live inside the
  package directory (`src/forgent/registry/agents/claude_code/`), which means
  they're wiped on every reinstall. Add an env var that points at an
  external directory (default `$HOME/.forgent/dynamic/`) and merge those into
  the registry on load.
- **Vector embedding column for the memory store.** FTS5 keyword recall is
  fast and dependency-free, but misses semantic matches ("auth bug" doesn't
  recall a memory tagged "credential failure"). Add an *optional* embedding
  column with a fallback to FTS5 when no embedding model is available.
  Recommended: voyage-3-lite or nomic-embed-text-v1.5 (small, free, no
  Anthropic dependency).
- **Memory pruning policy.** Right now memory grows forever. Add a
  configurable retention rule: keep last N sessions, keep last N tasks per
  agent, drop stale `note` entries after 30 days.

### Real ecosystem adapters

- **Real LangGraph adapter.** Today the `PythonFrameworkAdapter` ships
  *native* Python implementations of the workflow patterns (router,
  orchestrator, parallel, evaluator-optimizer, swarm, deep-orchestrator).
  This was a v0.1 shortcut to avoid the LangGraph dependency. Wire up real
  LangGraph nodes as an opt-in via `pip install forgent[langgraph]`.
- **Real CrewAI adapter.** Same pattern — opt-in via the `[crewai]` extra.
- **AutoGen adapter** (`pip install forgent[autogen]`).
- **AWS Bedrock Agents adapter** for users who want to keep everything in
  AWS.

### Distribution

- **Homebrew formula.** `brew install forgent` for Mac users who don't want
  pipx.
- **Docker image** at `ghcr.io/alialaayedi/forgent` so Linux/Windows users
  can `docker run` without setting up Python.
- **Anthropic MCP marketplace listing** once forgent is on PyPI.

## v0.3+ — longer horizon

Bigger bets that need design work before code.

- **Web dashboard** for browsing sessions, forged agents, memory recall, and
  routing decisions. Probably a small Astro + Tailwind app served by `forgent
  serve` that reads the SQLite store directly.
- **`forge_from_examples`** — generate a new specialist from a few input/output
  pairs the user provides, instead of from a task description. This is the
  classic "I have 5 examples of what I want, give me an agent that produces
  the 6th."
- **Cross-session memory linking.** When a session in project A produces
  output that's relevant to a session in project B, the memory store should
  surface it via the unified search index (with explicit project tags so the
  user can opt out).
- **Auto-refresh the curated catalog from upstream** weekly via a GitHub
  Action that pulls the latest agents from the source repos
  (wshobson/agents, VoltAgent, etc.) and opens a PR with new candidates.
- **Pluggable router strategies.** Today there's exactly one LLM router and
  one heuristic fallback. Make it easy to swap in custom routers (rule-based,
  embedding-similarity, learned-from-history) without forking the package.
- **Multi-tenant mode** for hosted/team deployments — separate memory stores
  per user, RBAC on the registry, audit logs.

## Won't do

Explicit non-goals so we don't waste cycles relitigating them.

- **A custom LLM SDK.** Forgent uses the official `anthropic` Python SDK and
  has no plans to roll its own client.
- **Hosted SaaS in this repo.** A hosted version of forgent might exist as a
  separate project, but the open source repo stays as a self-hostable library
  + MCP server. No telemetry, no phone-home, no required cloud account.
- **Replacing Claude Code.** Forgent is a *complement* to Claude Code, not a
  replacement. If a task needs filesystem access, shell commands, or
  multi-file edits, the right tool is Claude Code itself — forgent should
  recommend that, not try to do it.
- **An agent marketplace where contributors charge for their agents.** All
  curated agents in the catalog stay MIT-licensed and free. The contributor
  reward model (donations split with PR contributors via Open Collective) is
  about funding *maintenance*, not paywalling content.
- **Deep integration with any single proprietary platform.** Forgent should
  work the same way against any LLM provider. Bedrock, Vertex, and OAuth
  credential flows are *opt-in*, not the default.

## How priorities are set

Items marked v0.1.1 are bugs or papercuts — they get cut whenever 5+ are ready.
v0.2 items need more design work and ship together as a coherent release.
v0.3+ items are deliberately unscoped — open an issue if you want to scope
one and pull it forward.

The single biggest input to priority is **what hurts real users**. If you hit
a wall, file an issue. The roadmap exists to be rewritten.
