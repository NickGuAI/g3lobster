UV ?= uv
VENV := .venv
PYTHON := $(VENV)/bin/python

.PHONY: fmt lint test run install

install:
	$(UV) venv $(VENV)
	$(UV) pip install -e ".[dev]" --python $(VENV)/bin/python

fmt: install
	$(PYTHON) -m compileall g3lobster

lint: install
	$(PYTHON) -m py_compile $$(find g3lobster -name '*.py')

test: install
	$(PYTHON) -m pytest -q

run:
	@if [ ! -f "$(VENV)/bin/python" ]; then \
		$(UV) venv $(VENV) && $(UV) pip install -e ".[dev]" --python $(VENV)/bin/python; \
	fi
	$(PYTHON) -m g3lobster
