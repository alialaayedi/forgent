---
name: configure
description: Check or change the forgent install (channel, scope, hook profile, status line) by running forgent doctor and forgent setup.
argument-hint: "[doctor|setup|repair|uninstall]"
disable-model-invocation: true
allowed-tools: Bash(forgent doctor:*), Bash(forgent setup:*), Bash(forgent repair:*)
---

Help the user manage their forgent install. Requested action: $ARGUMENTS

1. Run `forgent doctor` and summarize anything marked warn or fail, including its suggested fix.
2. If the user asked to change the setup, run `forgent setup --dry-run` with the flags they want (`--channel plugin|mcp`, `--scope user|project|local`, `--hooks off|minimal|standard`, `--statusline/--no-statusline`, `--replace` to switch away from a conflicting install) and show the preflight plan.
3. Only after the user approves the plan, run the same command with `--yes` instead of `--dry-run`.
4. For `repair` or `uninstall`, run it with `--dry-run` first and apply only after the user agrees. Uninstall keeps memory databases.
5. Remind the user to start a new Claude Code session for plugin, hook, or MCP changes to take effect.
