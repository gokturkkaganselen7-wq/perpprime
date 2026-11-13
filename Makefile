PY := .venv/bin/python
export PYTHONPATH := src

.PHONY: help test doctor demo plan mcp audit clean

help:
	@echo "make test    - run the test suite"
	@echo "make doctor  - environment and safety checks"
	@echo "make demo    - full paper lifecycle (entry -> stop -> journal)"
	@echo "make mcp     - run the MCP stdio server"
	@echo "make audit   - registry vs skills/ on disk"
	@echo "make clean   - remove caches and the paper demo state"

test:
	$(PY) -m pytest

doctor:
	$(PY) -m cfos doctor

demo:
	$(PY) -m cfos demo

mcp:
	$(PY) -m cfos mcp

audit:
	$(PY) skills/cfos-router/scripts/audit_registry.py

clean:
	rm -rf .pytest_cache src/cfos/__pycache__ tests/__pycache__
	rm -rf data/demo
