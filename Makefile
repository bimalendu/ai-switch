PYTHON ?= python3
PODMAN ?= podman

.PHONY: test lint

test: lint
	$(PYTHON) -m unittest discover -s tests/unit -t . -v

lint:
	$(PYTHON) -m compileall -q ai_switch tests

# Needs podman. PODMAN="podman --connection NAME" selects a machine;
# CONTAINERS="fedora ubuntu" limits the distros (default: all six).
.PHONY: test-containers
test-containers:
	PODMAN="$(PODMAN)" sh tests/containers/run-matrix.sh $(CONTAINERS)
