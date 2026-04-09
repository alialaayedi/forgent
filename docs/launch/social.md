# Social launch drafts

Iterate on these. Each platform has its own conventions — these are starting points, not final copy.

## Twitter / X — single tweet (the hook)

```
i built forgent — a single MCP server that gives Claude a registry of
63 curated specialist subagents AND can synthesize brand-new ones on
demand.

one command, any task, any Claude environment.

MIT, all yours:
github.com/alialaayedi/forgent
```

**Image**: attach `assets/brand/png/social-preview-1280x640.png`.

**Why this works**: leads with the unique feature (synthesis), names the
audience (Claude users), gives the install in one URL. Lowercase wordmark
matches the brand voice.

## Twitter / X — thread version (5 tweets)

If you want to expand into a thread instead of one tweet:

**1/**
```
i built forgent — a meta-orchestrator that routes any task to the best
specialist subagent, AND grows new specialists on demand when none fit.

ships as a single MCP server. drops into Claude Code, Claude Desktop,
Cursor, Zed.

[image: social-preview.png]
```

**2/**
```
problem: the agent ecosystem is fragmented into three silos.

- Claude Code subagents (markdown, only run inside Claude Code)
- Python frameworks (LangGraph/CrewAI/AutoGen — code, lock-in)
- MCP servers (one tool per server, no orchestration)

forgent has adapters for all three. one entry point.
```

**3/**
```
the unique piece: the AgentForge.

if no curated agent fits your task, forgent asks Claude to *design* a
new specialist for it. the forged agent gets a real 400+ word system
prompt, capabilities, a category, and persists to disk.

next call uses it.
```

**4/**
```
also baked in:
- 63 hand-curated agents from the top public repos (wshobson, VoltAgent,
  0xfurai, lastmile-ai)
- SQLite + FTS5 memory that recalls relevant past context on every run
- LLM router with structured tool-use + heuristic fallback
- 8 MCP tools, no extra config
```

**5/**
```
MIT, 200KB wheel, 8/8 tests passing.

  pipx install forgent
  forgent forge "design SAML 2.0 SSO with Okta"

repo: github.com/alialaayedi/forgent
docs: github.com/alialaayedi/forgent/blob/main/docs/INTEGRATION.md
```

## LinkedIn

LinkedIn rewards longer, more "professional" framing. Don't be cute here.

```
I shipped a project today: forgent, a meta-orchestrator for AI agents.

The agent ecosystem in 2026 is fragmented into three silos that don't
talk to each other — Claude Code subagents, Python multi-agent
frameworks like LangGraph and CrewAI, and MCP servers. Each is great in
its own context, but a developer with a task currently has to pick a
silo before they can pick a solution.

Forgent is the meta-layer. One Python entry point takes any task,
classifies it via an LLM router, picks the best curated agent from a
registry of 63 specialists pulled from the highest-quality public
repos, and executes it through the matching ecosystem adapter.

The novel piece is the AgentForge: when no curated agent fits a task,
Forgent asks Claude to *design* a new specialist for it. The forged
agent gets a full 400-word system prompt, capabilities, a category,
and is persisted to disk. The orchestrator literally grows new
capabilities over time.

Other things baked in:
• A SQLite + FTS5 memory store that persists every task and recalls
  relevant past context on every new run
• An MCP server that drops into Claude Code, Claude Desktop, Cursor,
  Zed, and any other MCP client
• Async ecosystem adapters with a common ABC, so adding a new agent
  framework is a 50-line file
• A brand-themed CLI with the same forgent palette every other
  surface uses

Open-source under MIT. The contributor model is intentional and
unusual — donations to the project are pooled and shared with active
contributors via Open Collective. I want this to grow because the
people who improve it are materially rewarded for shipping.

Repo: github.com/alialaayedi/forgent

Built solo over a few intense sessions. Happy to talk architecture or
the contributor reward model in the comments.

#AI #OpenSource #Anthropic #Claude #DeveloperTools #Python
```

## r/ClaudeAI / r/LocalLLaMA / r/LangChain post

Reddit is allergic to marketing copy. Plain language wins.

**Title**: `I built forgent — a meta-orchestrator that grows its own Claude subagents on demand`

**Body**:
```
Hi all — wanted to share something I built and get feedback.

forgent is a Python project that does two things:

1. Routes any task to the best curated specialist agent from a registry
   of 63 (pulled from wshobson/agents, VoltAgent/awesome-claude-code-
   subagents, 0xfurai/claude-code-subagents, lastmile-ai/mcp-agent —
   all MIT/Apache, all attributed).

2. When no curated agent fits, asks Claude to *design* a new specialist
   for the task class. The forged agent gets a real system prompt and
   is persisted, so every future call of that kind uses it.

The whole thing ships as a single MCP server, so once you've installed
it once you can call it from Claude Code, Claude Desktop, or any other
MCP client without extra setup.

Other bits:
- SQLite + FTS5 memory that recalls past task context automatically
- Common Adapter ABC across Claude Code, Python frameworks, and MCP
- Brand-themed CLI for the people who care about that
- 8/8 smoke tests, MIT license, 200KB wheel

Repo: https://github.com/alialaayedi/forgent

Would love feedback on the AgentForge approach in particular — does
the synthesized-on-demand model make sense to people, or does
hand-curation still feel safer? I went back and forth on this.
```

## Discord (the 1-line drop)

For relevant Discord channels (Anthropic, MCP, AI engineering communities):

```
shipped forgent today — single MCP server that routes tasks across 63
curated specialist subagents AND forges new ones on demand. MIT, drops
into any Claude env. would love feedback: github.com/alialaayedi/forgent
```

## Posting order (pick one cycle)

If you want to do a coordinated launch in a single day:

1. **9:00 AM Pacific** — Show HN
2. **9:15 AM Pacific** — single tweet
3. **9:30 AM Pacific** — Reddit (r/ClaudeAI first, then r/LocalLLaMA, then r/LangChain spaced 30 min apart)
4. **10:00 AM Pacific** — Discord drops
5. **12:00 PM Pacific** — LinkedIn (LinkedIn engagement is highest at lunch + after work)
6. **All day** — answer comments on HN, Twitter, Reddit threads. Engagement signals to algos.

Don't post everywhere at once — you'll exhaust your attention budget.
Don't post if you can't be responsive for 4-6 hours after.
