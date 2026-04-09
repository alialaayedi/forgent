# Contributing

Thanks for your interest. This project welcomes:

- New curated agents from high-quality public repos (PR adding to `catalog.yaml` + a justification for why they belong)
- New ecosystem adapters (AutoGen, Semantic Kernel, Bedrock Agents, etc.)
- New MCP tools exposed via the server
- Bug fixes and tests

## Adding a curated agent

1. Find a strong candidate in a public repo. "Strong" means: clear trigger description, structured prompt, active maintenance, sane license (MIT/Apache).
2. Add an entry to `src/orchestrator/registry/catalog.yaml` with `name`, `ecosystem`, `category`, `capabilities`, `source_repo`, `source_path`, `description`.
3. If the source repo is in `sources/`, run `orchestrator vendor` to copy the markdown body in.
4. Open a PR. Include a one-paragraph rationale: what's missing in the existing catalog that this agent fills?

## Adding an ecosystem adapter

1. Subclass `orchestrator.adapters.base.Adapter`.
2. Implement `async def run(self, agent, task, context) -> AdapterResult`.
3. Wire it into `Orchestrator.__init__` under the matching `Ecosystem` key.
4. Add a smoke test in `tests/test_smoke.py` that exercises it without an API key.

## Local dev

```bash
make install      # creates .venv, installs editable, fixes the macOS .pth quirk
make test         # runs the smoke suite
make vendor       # copies source agent files into the registry
```

## House rules

- Python 3.10+, type hints on public APIs.
- No emojis in code or docs.
- Don't bypass `MemoryStore` for persistence — extend it instead.
- Don't import from `sources/` at runtime.
- Tests should pass without an API key (use the fake adapter pattern in `tests/test_smoke.py`).
