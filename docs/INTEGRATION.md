# Integration guide — bring the orchestrator to every Claude environment

Once you've installed the orchestrator (see the install steps below), the same
binary works across:

- **Claude Code** (any project on your machine)
- **Claude Desktop** (macOS / Windows)
- **Cursor**, **Zed**, **Windsurf**, and any other MCP-compatible client
- **Any other machine** — copy the wheel, run `pipx install`, done

## 1. Install once

```bash
cd /Users/alikareem/Documents/agent-orchestration
python3 -m build --wheel              # produces dist/agent_orchestrator-0.1.0-py3-none-any.whl
./scripts/install.sh                  # pipx-install + print registration commands
```

After this:
- `orchestrator` is on your `$PATH` (any directory)
- `orchestrator-mcp` is the MCP server entry point (any MCP client can spawn it)

You can also distribute the `.whl` file to any other machine and just run
`pipx install agent_orchestrator-0.1.0-py3-none-any.whl`.

## 2. Register with Claude Code

Claude Code reads MCP servers from `~/.claude/mcp_settings.json` (or per-project
`.mcp.json`). The CLI command below adds it for you:

```bash
# Pick up ANTHROPIC_API_KEY from your shell. Per-project memory means each
# directory you cd into has its own DB at ./orchestrator.db.
claude mcp add agent-orchestrator \
  --env ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  --env ORCHESTRATOR_DB=./orchestrator.db \
  -- $(which orchestrator-mcp)
```

Verify:

```bash
claude mcp list                       # should show agent-orchestrator
```

In any Claude Code session you can now ask:

> "Use the orchestrator to forge a specialist for writing Solana smart contracts, then run a task with it."

Claude will call `forge_agent`, then `run_task`, automatically.

## 3. Register with Claude Desktop

Edit the config file:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add (or merge into) the `mcpServers` block:

```json
{
  "mcpServers": {
    "agent-orchestrator": {
      "command": "/Users/YOU/.local/bin/orchestrator-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "ORCHESTRATOR_DB": "/Users/YOU/.orchestrator.db"
      }
    }
  }
}
```

Restart Claude Desktop. The orchestrator's tools appear in the slash-tools
menu and Claude can call them in any conversation.

## 4. Tools the MCP server exposes

| Tool | What it does |
|---|---|
| `run_task(task, auto_forge=False)` | Full route → dispatch → remember. Set `auto_forge=true` to grow a new specialist if confidence is low. |
| `forge_agent(task, name?, category?, force=False)` | Synthesize a brand-new specialist subagent for a task class. Persisted to disk and reused forever. |
| `list_agents(ecosystem?, category?)` | Browse the curated registry. |
| `search_agents(query, limit=10)` | Keyword search the registry. |
| `show_agent(name)` | Full system prompt + metadata for one agent. |
| `recall_memory(query, limit=5, type?)` | Query the project's memory store. |
| `memory_stats()` | Counts of what's currently in memory. |
| `route_only(task)` | Cheap dry-run — show which agent the orchestrator would pick. |

## 5. Per-project memory

The MCP server writes its memory DB to whatever path `ORCHESTRATOR_DB`
points at. The default is `./orchestrator.db` (relative to wherever the
client launched the server), so each project gets its own knowledge base.

To share memory across all projects on a machine, set
`ORCHESTRATOR_DB=$HOME/.orchestrator.db` in the Claude Code/Desktop env.

## 6. Forging new subagents — the killer feature

The orchestrator can grow new specialists on demand. Two paths:

**Explicit (recommended for stable results):**
```bash
orchestrator forge "design SAML 2.0 SSO integrations with Okta and Azure AD"
```
or in Claude:
> "Use forge_agent to create a specialist for SAML 2.0 SSO integrations."

The new agent is written to:
- `src/orchestrator/registry/dynamic.yaml` (metadata)
- `src/orchestrator/registry/agents/claude_code/<name>.md` (system prompt)

It's available immediately to every future call, and it survives restarts.

**Automatic (lower confidence triggers a forge):**
```bash
orchestrator run --auto-forge "design SAML 2.0 SSO integrations with Okta"
```
or in Claude:
> "Run this task with auto_forge enabled."

If the router's confidence is below 0.4, the orchestrator spawns a fresh
specialist for the task class and uses it. The specialist is then permanent.

This is how the orchestrator gets new capabilities over time without anyone
hand-editing `catalog.yaml`.

## 7. Updating

```bash
cd /Users/alikareem/Documents/agent-orchestration
git pull             # if you've moved this to a git repo
python3 -m build --wheel
pipx install --force dist/agent_orchestrator-*.whl
```

Forged agents in `dynamic.yaml` and `agents/claude_code/` survive upgrades
because they're inside the package directory — but if you reinstall from
the wheel they'll be replaced. To keep them safe across reinstalls, copy
`dynamic.yaml` to a backup before upgrading, or set
`ORCHESTRATOR_DYNAMIC_DIR` to an external path (planned for v0.2).
