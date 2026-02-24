SHELL := /bin/bash

.PHONY: compile check tests ci check-template check-locks

# Reusable Python interpreter for consistency
PYTHON ?= python3
TEMPLATES := python_cli python_fastapi python_library

# Compile project packages to bytecode to catch syntax errors early.
compile:
	$(PYTHON) -m compileall -q autodev

# Lint and type-check the core package.
check:
	$(PYTHON) -m ruff check autodev
	$(PYTHON) -m mypy autodev

# Run the repository test suite.
tests:
	$(PYTHON) -m pytest -q autodev/tests
	cd templates/python_fastapi && $(PYTHON) -m pytest -q tests
	cd templates/python_cli && $(PYTHON) -m pytest -q tests
	cd templates/python_library && $(PYTHON) -m pytest -q tests

# Full local CI-equivalent pass.
ci: compile check tests check-template check-locks

# Validate template CI workflow and docs parity against shared contract.
check-template:
	bash docs/ops/check_template_parity_audit.sh

# Verify template requirement locks are present and in sync with direct requirements.
check-locks:
	bash docs/ops/check_template_dependency_locks.sh