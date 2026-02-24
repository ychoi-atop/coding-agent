SHELL := /bin/bash

.PHONY: compile check check-fast check-strict tests tests-fast tests-strict ci ci-fast ci-strict fast strict release-check check-release-gates check-template check-locks benchmark-generate perf-smoke perf-strict

# Reusable Python interpreter for consistency
PYTHON ?= python3

# Compile project packages to bytecode to catch syntax errors early.
compile:
	$(PYTHON) -m compileall -q autodev

# Fast local-loop quality lane: lint only, no strict typing.
check-fast:
	$(PYTHON) -m ruff check autodev

# Strict local quality lane (legacy baseline behavior): lint + type-check.
check-strict:
	$(PYTHON) -m ruff check autodev
	$(PYTHON) -m mypy autodev

# Legacy compatibility lane.
check: check-strict

# Optional mypy strict lane for untyped definitions; non-blocking by default.
check-untyped-defs:
	$(PYTHON) -m mypy --check-untyped-defs autodev || true

# Run the repository unit tests only.
tests-fast:
	$(PYTHON) -m pytest -q autodev/tests

# Run repository tests + template tests (strict baseline behavior).
tests-strict:
	$(PYTHON) -m pytest -q autodev/tests
	bash docs/ops/run_template_tests.sh

# Legacy compatibility lane.
tests: tests-strict

# Fast local CI-equivalent pass for quick iteration.
ci-fast: compile check-fast tests-fast

# Strict local CI-equivalent pass (existing behavior).
ci-strict: compile check-strict tests-strict check-template check-locks

# Preserve existing command name for strict lane.
ci: ci-strict

# Generate timing benchmark (baseline vs optimized).
benchmark-generate:
	$(PYTHON) docs/ops/benchmark_generate_cycle.py

# Parse generated .autodev validation traces and compare against previous perf run.
perf-smoke:
	$(PYTHON) docs/ops/perf_smoke.py

# Strict perf gate with conservative regression thresholds.
perf-strict:
	$(PYTHON) docs/ops/perf_strict.py

# Validate template CI workflow and docs parity against shared contract.
check-template:
	bash docs/ops/check_template_parity_audit.sh

# Verify template requirement locks are present and in sync with direct requirements.
check-locks:
	bash docs/ops/check_template_dependency_locks.sh

# Release gates for release readiness.
check-release-gates:
	@test -f CHANGELOG.md || { echo "[FAIL] Missing CHANGELOG.md"; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "[FAIL] Working tree is dirty; commit or stash changes first."; exit 1; }

release-check: ci-strict check-untyped-defs check-release-gates
check-release: release-check

# Explicit aliases for local workflows.
fast: ci-fast
strict: check-release
