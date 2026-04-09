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
Set FontSize     40
Set FontFamily   "JetBrains Mono Bold"
Set Width        1920
Set Height       1200
Set Padding      48
Set TypingSpeed  55ms
Set PlaybackSpeed 1.0

# Brand-matched terminal theme
Set Theme { "name": "forgent", "background": "#071013", "foreground": "#dfe0e2", "cursor": "#eb5160", "selection": "#b7999c", "black": "#071013", "red": "#eb5160", "green": "#7ecf94", "yellow": "#b7999c", "blue": "#aaaaaa", "magenta": "#eb5160", "cyan": "#b7999c", "white": "#dfe0e2", "brightBlack": "#0e1a1f", "brightRed": "#eb5160", "brightGreen": "#7ecf94", "brightYellow": "#b7999c", "brightBlue": "#aaaaaa", "brightMagenta": "#eb5160", "brightCyan": "#b7999c", "brightWhite": "#dfe0e2" }

# Hidden bootstrap — clean prompt
Hide
Type "clear"
Enter
Show

Sleep 600ms

# The whole demo in one line: Claude using forgent via MCP
Type `claude "use forgent to find kubernetes specialists"`
Sleep 300ms
Enter
Sleep 18000ms
