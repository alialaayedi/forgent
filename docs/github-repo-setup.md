# GitHub repo setup — copy/paste cheat sheet

Everything you need to push, configure, and tune the GitHub repo for forgent.
None of these commands are run automatically — they all touch your global
GitHub identity, so you should do them yourself with eyes on.

## 1. Create the repo and push

```bash
cd /Users/alikareem/Documents/agent-orchestration

gh repo create forgent \
  --public \
  --source=. \
  --remote=origin \
  --description "forgent — meta-orchestrator that grows its own AI subagents on demand. Routes any task across Claude Code subagents, Python frameworks, and MCP servers." \
  --push
```

This creates the repo, sets `origin` to it, and pushes both commits in one shot.

## 2. Add topics (improves discoverability)

GitHub topics are how people find your repo via search. Add the right tags and your project shows up in `topic:claude`, `topic:mcp`, `topic:agents`, etc.

```bash
gh repo edit --add-topic ai-agents
gh repo edit --add-topic claude
gh repo edit --add-topic claude-code
gh repo edit --add-topic mcp
gh repo edit --add-topic mcp-server
gh repo edit --add-topic anthropic
gh repo edit --add-topic agent-orchestration
gh repo edit --add-topic llm-agents
gh repo edit --add-topic langgraph
gh repo edit --add-topic crewai
gh repo edit --add-topic python
gh repo edit --add-topic cli
```

## 3. Set the social preview image

GitHub renders a custom card when your repo URL is shared on Twitter, LinkedIn, Discord, etc. The default is a generic GitHub card. Replace it with the brand image.

You'll need a PNG (GitHub doesn't accept SVG for social previews). Build it:

```bash
brew install librsvg                       # one-time, for rsvg-convert
./scripts/build-assets.sh                  # produces assets/brand/png/
```

Then upload via the web UI:

> github.com/YOU/forgent → Settings → Social preview → Upload an image
> Use `assets/brand/png/social-preview-1280x640.png`

(There's no `gh` command for this — GitHub's API doesn't expose it yet.)

## 4. Set the org/user avatar (optional)

If forgent grows enough that you create a GitHub organization for it, use the brand mark as the avatar:

> Org settings → Profile picture → Upload
> Use `assets/brand/png/icon-mark-512.png`

## 5. Pin a release

Tag the first release so the wheel artifact in CI gets attached to a versioned release:

```bash
git tag v0.1.0
git push origin v0.1.0

# Then create the GitHub release with the wheel attached
gh release create v0.1.0 \
  --title "forgent v0.1.0 — first public release" \
  --notes "$(cat <<'EOF'
The first public release of forgent.

## What's in it

- 63 hand-curated specialist agents across 11 categories
- AgentForge — synthesizes brand-new specialists on demand via Claude
- LLM-based task router with structured tool-use + heuristic fallback
- SQLite + FTS5 memory store with full-text recall
- Three async ecosystem adapters: Claude Code, Python frameworks, MCP servers
- Stdio MCP server with 8 tools for any MCP client
- Typer CLI, shippable wheel, one-shot install script

## Install

\`\`\`bash
pipx install forgent
\`\`\`

Or grab the wheel from the assets below.

## Register with Claude

See [docs/INTEGRATION.md](docs/INTEGRATION.md) for the full guide.
EOF
)" \
  dist/forgent-0.1.0-py3-none-any.whl
```

## 6. Enable GitHub Sponsors (for the contributor reward model)

The README's "Support & contributor rewards" section references GitHub Sponsors and Open Collective. Until you enable them, the badges and the reward model are aspirational only.

**GitHub Sponsors** (one-time setup, ~30 min):
1. Go to <https://github.com/sponsors>
2. Click "Join the waitlist" or "Set up" if you already have access
3. Complete the identity verification (Stripe Connect under the hood)
4. Pick tiers — recommended: $5/mo (supporter), $25/mo (sponsor), $100/mo (gold)
5. Once approved, your `.github/FUNDING.yml` becomes live

**Open Collective** (free for open source, ~10 min):
1. Go to <https://opencollective.com/create>
2. Pick "Open source project" → "Apply for fiscal hosting"
3. Use the [Open Source Collective](https://opencollective.com/opensource) as your fiscal host (free, takes 1-3 days for approval)
4. Slug: `forgent`
5. Once live, edit `.github/FUNDING.yml` and replace the placeholder slug

After both are set up, edit `.github/FUNDING.yml` and commit:

```yaml
github: [your-actual-github-handle]
open_collective: forgent
```

## 7. Post-launch checklist

After `gh repo create ... --push` succeeds:

- [ ] Watch the CI run (`gh run watch`) — it should pass on push
- [ ] Add topics (step 2 above)
- [ ] Upload social preview (step 3 above)
- [ ] Tag and release v0.1.0 (step 5 above)
- [ ] Verify the README banner renders correctly on the public repo page
- [ ] Verify the FUNDING.yml shows a "Sponsor" button (only if step 6 is done)
- [ ] Tweet/post a launch with `assets/brand/png/social-preview-1280x640.png`
- [ ] Submit to <https://github.com/punkpeye/awesome-mcp-servers>
- [ ] Show HN: lead with the forge feature, link the demo GIF

## Description variants for different surfaces

| Surface | Character limit | Text |
|---|---|---|
| **GitHub repo description** | 350 | `forgent — meta-orchestrator that grows its own AI subagents on demand. Routes any task across Claude Code subagents, Python frameworks, and MCP servers.` |
| **PyPI summary** | 200 | `Meta-orchestrator that grows its own AI subagents on demand. Routes tasks across Claude Code, Python frameworks, and MCP servers.` |
| **HN title** | 80 | `Show HN: forgent – an MCP server that grows its own AI subagents on demand` |
| **Tweet hook** | 240 | `i built forgent — a single MCP server that gives Claude a registry of 63 curated specialist subagents AND can synthesize brand-new ones on demand. one command, any task, any Claude environment. MIT, all yours: github.com/.../forgent` |
| **README sub-tagline** | unlimited | `grow your own AI subagents on demand` |

## What NOT to do

- Don't push to `main` directly after launch — set up branch protection so CI gates merges
- Don't accept PRs without running `make test` locally first (until CI is green)
- Don't enable GitHub Discussions until you have at least 50 stars (empty discussion tabs look dead)
- Don't add a "Star History" chart until you have a star history (empty graphs are sad)
