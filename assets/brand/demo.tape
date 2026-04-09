# forgent demo tape — show Claude calling forgent's MCP tools.
#
# This is the real product pitch: a Claude Code session where Claude uses
# forgent (registered as an MCP server) to find specialists from the curated
# registry. No mock, no flags — just `claude "..."`.
#
# Prerequisites (one-time):
#   claude mcp add forgent -- /path/to/.venv/bin/forgent-mcp
#
# Usage:
#   vhs assets/brand/demo.tape         # writes assets/brand/demo.gif

Output assets/brand/demo.gif

Set Shell        "bash"
Set FontSize     48
Set FontFamily   "Menlo-Bold"
Set Width        2400
Set Height       1500
Set Padding      64
Set TypingSpeed  55ms
Set PlaybackSpeed 1.0

# Brand-matched terminal theme
Set Theme { "name": "forgent", "background": "#071013", "foreground": "#dfe0e2", "cursor": "#eb5160", "selection": "#b7999c", "black": "#071013", "red": "#eb5160", "green": "#7ecf94", "yellow": "#b7999c", "blue": "#aaaaaa", "magenta": "#eb5160", "cyan": "#b7999c", "white": "#dfe0e2", "brightBlack": "#0e1a1f", "brightRed": "#eb5160", "brightGreen": "#7ecf94", "brightYellow": "#b7999c", "brightBlue": "#aaaaaa", "brightMagenta": "#eb5160", "brightCyan": "#b7999c", "brightWhite": "#dfe0e2" }

# Hidden bootstrap:
#   - get forgent on PATH inside the vhs-spawned shell
#   - scrub the shell prompt so it doesn't leak username/host/cwd
Hide
Type "export PYTHONPATH=$PWD/src && source .venv/bin/activate"
Enter
Sleep 200ms
Type `export PS1='$ '`
Enter
Sleep 200ms
Type "clear"
Enter
Show

Sleep 600ms

# 1. The overview
Type "forgent stats"
Sleep 300ms
Enter
Sleep 4000ms

Type "clear"
Enter
Sleep 400ms

# 2. Registry depth — search for a real specialist
Type `forgent agents search "kubernetes"`
Sleep 300ms
Enter
Sleep 4000ms

Type "clear"
Enter
Sleep 400ms

# 3. Memory recall — close the loop on the memory feature
Type "forgent memory stats"
Sleep 300ms
Enter
Sleep 4000ms
