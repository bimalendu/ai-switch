PYTHON ?= python3

.PHONY: test lint

test: lint
	$(PYTHON) -m unittest discover -s tests/unit -t . -v

lint:
	$(PYTHON) -m compileall -q ai_switch tests
