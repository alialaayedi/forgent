# forgent — brand guide

A small project deserves a strong identity. Use this guide whenever you're touching anything user-facing — README, CLI output, social posts, slides, future website.

## Palette

| Token | Hex | Role |
|---|---|---|
| **ink black** | `#071013` | primary background, deep ground tone |
| **lobster pink** | `#eb5160` | primary brand accent, the forge spark, primary CTA |
| **rosy taupe** | `#b7999c` | secondary accent, warm muted neutral, default border |
| **silver** | `#aaaaaa` | metadata, dividers, dim text |
| **alabaster grey** | `#dfe0e2` | primary foreground on dark, light surface |

The canonical files live in [`assets/brand/`](../assets/brand) — `colors.css`, `colors.scss`, and `colors.json`. Always read from those, never hardcode hexes elsewhere.

## Visual identity

**Mood**: dark, warm, premium-but-approachable, slightly mysterious. *Not* corporate blue. *Not* generic dev-tool grey.

**The story the colors tell**: an ink-black forge with a single pink spark — exactly what the project does (one orchestrator that grows new specialists from nothing). The taupe is the cooling metal. The silver is the anvil. The alabaster is the page where the new agent lands.

**One rule**: lobster pink is the spark. Use it sparingly. If it's everywhere, it's nowhere.

## Semantic mapping

When in doubt, use these aliases instead of raw palette colors:

| Semantic token | Color | Where to use |
|---|---|---|
| `bg` | ink black | page/terminal background, primary surface |
| `bg-elevated` | `#0e1a1f` | cards, panels, slightly raised surfaces |
| `fg` | alabaster grey | primary text on dark backgrounds |
| `fg-muted` | silver | secondary text, metadata, timestamps |
| `fg-secondary` | rosy taupe | tertiary text, descriptions |
| `accent` | lobster pink | CTAs, brand moments, primary borders, key numbers |
| `accent-quiet` | rosy taupe | secondary borders, tag chips, hover states |
| `border` | rosy taupe | default container borders |
| `border-strong` | lobster pink | important containers (the forge output, the result panel) |

## Typography

forgent's wordmark is set in a heavy sans (Inter, SF Pro Display, or any system-ui equivalent at weight 800). Lowercase only. No tagline in the wordmark itself — let the surrounding context carry the message.

The CLI output uses your terminal's default monospace font; we don't ship a custom font. Code samples in docs use `ui-monospace, 'SF Mono', Menlo, Consolas, monospace`.

## Don't

- Don't tint the lobster pink (no salmon, no hot pink, no maroon — keep it `#eb5160` exactly)
- Don't use rainbow gradients — only the brand spark gradient (lobster pink → rosy taupe → alabaster)
- Don't put pink on pink — always layer it on ink black or alabaster
- Don't capitalize the wordmark in body text (`forgent`, never `Forgent` — except as a class name in code)
- Don't add emojis to brand surfaces — the project deliberately avoids them

## Do

- Lead every brand surface with a moment of lobster pink (a single accent, a header underline, an icon)
- Use silver for `dim` metadata so the eye knows it's secondary at a glance
- Pair rosy taupe with monospace text for "this is a command" energy
- Keep whitespace generous around the wordmark — minimum padding equal to the cap height

## Assets

- [`assets/brand/banner.svg`](../assets/brand/banner.svg) — README hero, 1200×360
- [`assets/brand/icon.svg`](../assets/brand/icon.svg) — square logo, 512×512 (use as GitHub avatar, npm/PyPI icon, social card)
- [`assets/brand/colors.css`](../assets/brand/colors.css) — CSS variables, source of truth
- [`assets/brand/colors.scss`](../assets/brand/colors.scss) — SCSS variables
- [`assets/brand/colors.json`](../assets/brand/colors.json) — programmatic access (the CLI reads this)

## Voice & tone

Match the visual identity:

- **Confident** — say what the thing does in one sentence, then prove it
- **Specific** — "63 curated agents", "forges new specialists on demand", not "many integrations"
- **Honest** — name the limits, name the moats, don't oversell
- **No marketing fluff** — no "revolutionary", no "world-class", no "unleash"
- **Lowercase wordmark in prose**, sentence-case headers

A good README sentence: *"forgent grows its own AI subagents on demand."*
A bad one: *"Forgent is a revolutionary AI orchestration platform that empowers developers to unleash the full potential of their agentic workflows."*
