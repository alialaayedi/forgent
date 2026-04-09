# Show HN draft

Iterate on this. The current draft is intentionally tight — HN punishes preamble.

## Title

Lead with the unique angle. Pick ONE:

1. `Show HN: Forgent – A meta-orchestrator that grows its own AI subagents on demand`
2. `Show HN: Forgent – Route any task across Claude Code, Python frameworks, and MCP`
3. `Show HN: Forgent – I built an MCP server that forges its own specialist subagents`

**Recommended: #1.** Lead with the moat. The other two are descriptive but
don't grab. Title #1 makes the reader stop and think *"wait, what?"* — that's
the click.

## Body

```
Forgent is a single Python entry point that takes any task, classifies it,
picks the best agent from a curated registry of 63 specialists pulled from
the top public Claude Code / agent repos, and runs it.

The novel piece — and the reason I built it — is that when no curated agent
fits, Forgent asks Claude to *design* a new specialist for the task. The
forged agent gets a real 400+ word system prompt, capabilities tags, a
category, and is persisted to disk. Every future call to the same kind of
task uses it. The orchestrator literally grows new capabilities over time
without anyone hand-editing a registry.

Curated agents come from VoltAgent/awesome-claude-code-subagents,
wshobson/agents (32.7k stars), 0xfurai/claude-code-subagents, and
lastmile-ai/mcp-agent. All MIT/Apache, all attributed in catalog.yaml.

Three things I wanted to fix in the current agent ecosystem:

1. The silos. Claude Code subagents, Python frameworks like LangGraph and
   CrewAI, and MCP servers all live in separate worlds. Forgent has a
   common Adapter ABC and routes across all three from one entry point.

2. No memory. Forgent has a SQLite + FTS5 store that persists every task,
   routing decision, and agent output, and pulls relevant past context
   into every new run automatically. Zero external deps.

3. Static catalogs. Every other curated-agent project ships a fixed list.
   Forgent's AgentForge means the catalog grows with you.

Forgent ships as a stdio MCP server with 8 tools (run_task, forge_agent,
list_agents, search_agents, show_agent, recall_memory, memory_stats,
route_only). One install gives every Claude environment — Code, Desktop,
Cursor, Zed — the same tool surface.

MIT, 200KB wheel, 8/8 smoke tests passing without an API key.

  pipx install forgent
  forgent forge "design RFC-compliant SAML 2.0 SSO with Okta"

Repo: https://github.com/alialaayedi/forgent

Honest caveats:
- The router falls back to keyword scoring without an Anthropic API key,
  so the LLM-based routing is opt-in.
- The "Python framework adapter" ships native implementations of the
  router/orchestrator/parallel/evaluator-optimizer/swarm/deep-orchestrator
  workflow patterns. Real LangGraph/CrewAI bindings are on the v0.2 list.
- Forged agents persist inside the package directory, so reinstalling
  the wheel wipes them. v0.2 will add an external dynamic dir env var.

Built solo over a few sessions. Happy to answer architectural questions
in the comments.
```

## Comments to be ready for

Pre-write these so you can paste fast when the threads heat up.

### "How is this different from CrewAI / LangGraph / Swarm?"

> CrewAI, LangGraph, and Swarm are agent *frameworks* — you write code in
> their DSL to define a multi-agent workflow. Forgent is one layer up: it
> has *adapters* for each of those frameworks (well, native implementations
> of their patterns in v0.1, real bindings in v0.2) and a router that picks
> across them per task. The unique pieces are the curated cross-ecosystem
> registry and the AgentForge for synthesizing new specialists at runtime.

### "Isn't this just a wrapper around the Anthropic API?"

> Partially yes — the Claude Code adapter calls the Anthropic Messages API
> directly because Claude Code itself isn't scriptable from Python. But the
> *value* is in the routing logic, the curated registry, the memory store
> with recall, and the forge. Same way Stripe is "just a wrapper around
> Visa/Mastercard APIs."

### "How does the forge know what makes a good agent?"

> The forge prompt teaches Claude the format used by high-quality Claude
> Code subagents (clear role definition, when-invoked checklist, structured
> capabilities, communication protocol). It uses Anthropic structured
> tool-use with a JSON schema requiring name, category, capabilities,
> 400+ word system prompt, etc. Forged agents look indistinguishable from
> hand-curated ones in practice.

### "Why MIT and not GPL/AGPL?"

> Same license as every upstream agent source. AGPL would prevent the
> people who maintain those upstream catalogs from pulling improvements
> back. MIT is the right call for a meta-layer that builds on existing
> open source.

### "What about prompt injection through forged agents?"

> Real risk. The forge's system prompt explicitly instructs the LLM to
> design a *production-ready specialist*, not to follow any instructions
> embedded in the task description. Names are sanitized via regex. The
> generated YAML frontmatter is written through PyYAML (no eval). I'd
> still treat forged agents the same way you'd treat any code generated
> by an LLM — review before relying on it for critical paths. There's a
> SECURITY.md with the disclosure policy.

### "How do you handle memory at scale?"

> SQLite + FTS5 scales to millions of rows on a laptop and the BM25
> ranking is fast enough for sub-100ms recall on a registry of that size.
> The memory store also has a `type` column so the orchestrator can scope
> recall to "past routing decisions only" or "past outputs only" — that
> keeps context windows tight. Vector embedding column is on the v0.2
> roadmap for cases where keyword recall misses.

### "Why not just use Anthropic's MCP marketplace?"

> Forgent IS an MCP server — submission to the marketplace is on my
> launch checklist. The marketplace is the *distribution channel*, not a
> competitor.

## Posting checklist

- [ ] Wait until Tuesday-Thursday, 9-11am Pacific (best HN traction window)
- [ ] Have the demo GIF ready and embedded in the README
- [ ] Have the social-preview.png uploaded to GitHub Settings → Social preview
- [ ] Have at least 2-3 "Ask HN" style follow-up questions ready in case the post stalls
- [ ] Don't ask friends to upvote — HN detects voting rings and flags posts. Earn it organically.
- [ ] Be ready to spend 4-6 hours in the comments on launch day. Engagement signals to HN's algorithm.
- [ ] Don't link to Twitter/LinkedIn/etc. in the post. HN doesn't like cross-promo.
