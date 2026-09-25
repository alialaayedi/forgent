# Integration guide: forgent in every Claude environment

One package, two surfaces:

- **Claude Code**: the `forgent@forgent` plugin (MCP server + skills + hooks), or a bare MCP server
- **Claude Desktop, Cursor, Zed, Cline, Continue, Windsurf**, and any other MCP client: the `forgent-mcp` stdio server

## 1. Install once

```bash
pipx install forgent          # or: uv tool install forgent
```

This puts `forgent`, `forgent-mcp`, and `forgent-statusline` on your `$PATH`.

## 2. Claude Code

```bash
forgent setup
```

The wizard detects existing installs, lets you choose the channel (`plugin` or `mcp`), scope (`user`, `project`, `local`), and hook profile, shows a preflight plan, and records what it changed so `forgent doctor`, `forgent repair`, and `forgent uninstall` stay safe. See the README's Install section for the non-interactive flags and the native `/plugin` commands.

Export `ANTHROPIC_API_KEY` in your shell profile. Claude Code passes its environment to the MCP server, so the key never needs to be written into `~/.claude.json`. Memory is per project: the server uses `./forgent.db` in the directory Claude Code was started from, unless `FORGENT_DB` is set.

Verify in a new session:

```
/plugin list          # forgent@forgent enabled (plugin channel)
/mcp                  # forgent connected
```

Then ask for a plan with `/forgent:plan add a refund endpoint to the Stripe integration`, or just describe a non-trivial task; Claude calls `advise_task` on its own.

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
    "forgent": {
      "command": "/Users/YOU/.local/bin/forgent-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "FORGENT_DB": "/Users/YOU/.forgent.db"
      }
    }
  }
}
```

Claude Desktop does not inherit your shell environment, so the key goes in the config here. Restart Claude Desktop. forgent's tools appear in the slash-tools
menu and Claude can call them in any conversation.

## 4. Tools the MCP server exposes

| Tool | What it does |
|---|---|
| `advise_task(task, auto_forge=True, budget_ms?, budget_usd?)` | Route + plan; returns a PlanCard for the host to execute. Forges a new pack when routing confidence is low. |
| `revise_plan(session_id, findings)` | Amend a PlanCard with mid-flight findings. |
| `report_outcome(session_id, success, notes?)` | Close the loop; the next plan for the same pack sees it. |
| `memory_view(path, limit=10)` | Pull-based recall over `/outcomes/`, `/notes/`, `/sessions/`, `/agents/`. |
| `memory_write(path, content)` | Leave a breadcrumb note, usually under `/notes/<topic>`. |
| `forge_agent(task, name?, category?, force=False)` | Synthesize a new knowledge pack for a task class. |
| `list_agents(ecosystem?, category?)` / `search_agents(query)` / `show_agent(name)` | Browse the registry. |
| `recall_memory(query, limit=5, type?)` / `memory_stats()` | Ad-hoc FTS recall and counts. |
| `route_only(task)` | Just the routing decision, no plan. |

## 5. Per-project memory

The MCP server writes its memory DB to whatever path `FORGENT_DB`
points at. The default is `./forgent.db` (relative to wherever the
client launched the server), so each project gets its own knowledge base.

To share memory across all projects on a machine, set
`FORGENT_DB=$HOME/.forgent.db` in the Claude Code/Desktop env.

## 6. Forging new subagents — the killer feature

forgent can grow new knowledge packs on demand. Two paths:

**Explicit (recommended for stable results):**
```bash
forgent forge "design SAML 2.0 SSO integrations with Okta and Azure AD"
```
or in Claude:
> "Use forge_agent to create a specialist for SAML 2.0 SSO integrations."

The new agent is written to:
- `src/forgent/registry/dynamic.yaml` (metadata)
- `src/forgent/registry/agents/claude_code/<name>.md` (system prompt)

It's available immediately to every future call, and it survives restarts.

**Automatic (on by default):** `advise_task` and `forgent advise` forge a
new pack when the router's confidence is below 0.4, then plan with it. Pass
`--no-forge` (CLI) or `auto_forge=false` (MCP) to turn this off.

This is how forgent gets new capabilities over time without anyone
hand-editing `catalog.yaml`.

## 7. Updating

```bash
pipx upgrade forgent       # or: uv tool upgrade forgent
forgent setup              # re-run to update the plugin and hook profile
forgent doctor
```

Forged packs in `dynamic.yaml` and `agents/claude_code/` live inside the
package directory, so a reinstall replaces them. Back up `dynamic.yaml`
and the forged `.md` files before reinstalling.
