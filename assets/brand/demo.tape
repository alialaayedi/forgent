# forgent demo tape — record the killer flow as a brand-themed terminal GIF.
#
# Usage:
#   brew install vhs                 # https://github.com/charmbracelet/vhs
#   vhs assets/brand/demo.tape       # writes assets/brand/demo.gif
#
# The Set Theme block uses the forgent palette so the recording matches every
# other brand surface. Adjust Sleep durations to taste — longer = more readable
# but a heavier GIF.

Output assets/brand/demo.gif

Set Shell      "bash"
Set FontSize   16
Set FontFamily "JetBrains Mono"
Set Width      1280
Set Height     760
Set Padding    32
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0

# Brand-matched terminal theme — exact forgent palette
Set Theme { "name": "forgent", "background": "#071013", "foreground": "#dfe0e2", "cursor": "#eb5160", "selection": "#b7999c", "black": "#071013", "red": "#eb5160", "green": "#7ecf94", "yellow": "#b7999c", "blue": "#aaaaaa", "magenta": "#eb5160", "cyan": "#b7999c", "white": "#dfe0e2", "brightBlack": "#0e1a1f", "brightRed": "#eb5160", "brightGreen": "#7ecf94", "brightYellow": "#b7999c", "brightBlue": "#aaaaaa", "brightMagenta": "#eb5160", "brightCyan": "#b7999c", "brightWhite": "#dfe0e2" }

Hide
Type "clear"
Enter
Show

Sleep 600ms

# 1. The overview — show the breadth in one frame
Type "forgent stats"
Sleep 400ms
Enter
Sleep 2500ms

# 2. Search — prove the registry has depth
Type `forgent agents search "kubernetes security"`
Sleep 400ms
Enter
Sleep 3000ms

# 3. The killer move — forge a brand-new specialist on demand
Type `forgent forge "design RFC-compliant SAML 2.0 SSO with Okta and Azure AD"`
Sleep 400ms
Enter
Sleep 5500ms

# 4. The forge persisted — show that the new specialist now lives in the registry
Type `forgent agents search "saml"`
Sleep 400ms
Enter
Sleep 3000ms

# 5. Memory recall — close the loop
Type `forgent memory stats`
Sleep 400ms
Enter
Sleep 2500ms

Sleep 1500ms
