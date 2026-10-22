# The project venv when present (it has the optional mcp extra); override with PYTHON=...
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: help test lint

help:
	@echo "make test  - run offline unit tests (never contacts Odoo)"
	@echo "make lint  - byte-compile sources and tests"

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -t . -v

lint:
	$(PYTHON) -m compileall -q src tests
