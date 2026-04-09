# Convenience targets. The macOS chflags step works around an issue where
# files created inside Claude Code's sandbox can land with the UF_HIDDEN
# attribute set, which causes Python's site.py to silently skip the editable
# install .pth file. Harmless on Linux/Windows.

VENV ?= .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

.PHONY: install vendor test refresh-sources clean

install:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q -e ".[dev]"
	@if [ "$$(uname)" = "Darwin" ]; then \
		find $(VENV)/lib -name '__editable__.*.pth' -exec chflags nohidden {} \; 2>/dev/null || true; \
	fi
	@$(PYTHON) -c "import forgent; print('forgent package importable')"

vendor: install
	$(VENV)/bin/forgent vendor

test: install
	$(PYTHON) -m pytest tests/ -v

refresh-sources:
	rm -rf sources/wshobson-agents sources/voltagent-subagents sources/furai-subagents sources/lastmile-mcp-agent
	mkdir -p sources
	cd sources && git clone --depth 1 https://github.com/wshobson/agents.git wshobson-agents
	cd sources && git clone --depth 1 https://github.com/VoltAgent/awesome-claude-code-subagents.git voltagent-subagents
	cd sources && git clone --depth 1 https://github.com/0xfurai/claude-code-subagents.git furai-subagents
	cd sources && git clone --depth 1 https://github.com/lastmile-ai/mcp-agent.git lastmile-mcp-agent

clean:
	rm -rf $(VENV) build dist *.egg-info src/*.egg-info forgent.db
