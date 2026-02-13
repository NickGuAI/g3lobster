PYTHON ?= python3

.PHONY: fmt lint test run

fmt:
	$(PYTHON) -m compileall g3lobster

lint:
	$(PYTHON) -m py_compile $$(find g3lobster -name '*.py')

test:
	$(PYTHON) -m pytest -q

run:
	$(PYTHON) -m g3lobster
