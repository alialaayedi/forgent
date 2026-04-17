# forgent roadmap

forgent is a planning + knowledge layer for AI coding agents. Its competitive
wedge: curated specialist knowledge + planner + outcome-aware memory + MCP
native. v0.4 ships the first full implementation across all four. This
document tracks what's next.

## Shipped in v0.4.0

- Full status-line rewrite: 4 render modes (minimal / powerline / capsule /
  compact), 3 themes (dark / light / highcontrast), priority-based flex
  collapsing with multi-line wrap, segments for agent / wins / notes /
  forged / cwd / git / context bar / cost / rate limits / tokens I/O /
  model / time / session age.
- Auto-compact configuration via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
  (default 60% on first run; override with `forgent autocompact <n>`).
- Routing transparency: top-3 runner-up packs surfaced in every PlanCard.
- Multi-agent plan graphs: `PlanCard.subplans` is a DAG of child plans when
  the router picks `sequential` / `parallel` / `evaluator-optimizer`.
- Replan-on-deviation: `revise_plan` MCP tool preserves the session id and
  versions each plan (v1, v2, ...).
- Cost/latency budgets: `advise_task(budget_ms, budget_usd)` downshifts to
  the heuristic planner when the LLM path would blow the budget.
- Evidence-backed outcomes: `report_outcome(verify=True)` runs the verifier
  (git diff / tests / lint / CI) and overrides self-reported success.
- Hybrid retrieval: `FORGENT_EMBED_MODEL=<model>` opts memory into
  embedding-backed recall with reciprocal-rank fusion over BM25 + semantic.
- Marketplace scaffold: `forgent install <name-or-url>` clones a pack
  from GitHub, vendors it into the local registry.
- IDE surfaces: `forgent setup-ide <cursor|cline|zed|continue|claude-code|
  claude-desktop>` emits ready-to-paste MCP config.
- Team memory tagging: `forgent team init <name>` tags future writes with
  a team_id (soft scope; real RBAC needs a server -- see below).

## Deferred (v0.5+)

### Hosted public leaderboard

`forgent eval` ships a local runner scaffold in v0.4. A public leaderboard
comparing forgent PlanCards against raw Claude Code, Task Master, Cline plan
mode, and Aider on SWE-bench-lite would own the narrative in this space --
nobody has credible numbers. The blocker is hosting infra: domain, CI to
run evals on every release, a static site to publish results.

### Signed pack distribution

`forgent install` currently trusts any git URL you give it. For a real
marketplace we need cryptographic signing of catalog entries + revocation
so malicious packs can't ship.

### Team memory server with real RBAC

v0.4 tags writes with `team_id` but the DB is still "whoever has the file
can read it." True multi-team RBAC requires a backend server handling auth
and access control. This is an enterprise wedge but meaningful effort
beyond a single PR.

### Full SWE-bench runner

`forgent eval run` is a scaffold. The full implementation clones the
SWE-bench-lite dataset, iterates tasks, calls advise + host execution
(via the `claude` CLI), compares generated diffs against known-good ones,
and produces a JSON + markdown report. Needs dataset handling + robust
task isolation.

## Out of scope (permanent)

- Running agents inside forgent. v1 had ecosystem adapters with their own
  tool-use loops. v2 explicitly deleted them. The host LLM is in charge;
  forgent plans and remembers.
- Replacing the host LLM. forgent routes and plans -- it does not
  substitute for Claude Code, Cursor, Cline, etc.
- Non-coding tasks. The agent catalog is coding-specialized by design.
