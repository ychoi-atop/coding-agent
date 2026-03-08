SHELL := /bin/bash

.PHONY: compile check check-fast check-strict tests tests-fast tests-strict ci ci-fast ci-strict fast strict release-check check-release-gates check-release-autonomous check-release-autonomous-strict check-template check-locks check-docs check-status-hooks benchmark-generate perf-smoke perf-strict perf-compare perf-compare-smoke untyped-check test-backend demo-scorecard demo-bootstrap demo-bootstrap-serve smoke-autonomous-e2e

# Reusable Python interpreter for consistency
PYTHON ?= python3

# Canonical status-hook event used for docs automation drift checks.
STATUS_HOOK_EVENT ?= av4.kickoff.started

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

# Compatibility alias used by legacy CI entrypoints.
untyped-check:
	$(PYTHON) -m mypy --check-untyped-defs autodev

# Run the repository unit tests only.
tests-fast:
	$(PYTHON) -m pytest -q autodev/tests

# Run repository tests + template tests (strict baseline behavior).
tests-strict:
	$(PYTHON) -m pytest -q autodev/tests
	bash docs/ops/run_template_tests.sh

# Legacy compatibility lane.
tests: tests-strict

# Compatibility test target used by older workflows.
test-backend:
	@TARGET_TESTS="$(BACKEND_TEST_ARGS)"; \
	TARGET_BASE=$${TARGET_TESTS%%::*}; \
	if [ -z "$$TARGET_TESTS" ]; then \
		$(PYTHON) -m pytest -q autodev/tests; \
	elif [ -f "$$TARGET_TESTS" ] || [ -d "$$TARGET_TESTS" ] || [ -f "$$TARGET_BASE" ]; then \
		$(PYTHON) -m pytest -q "$$TARGET_TESTS"; \
	else \
		echo "[WARN] Missing test target $$TARGET_TESTS; running default test suite"; \
		$(PYTHON) -m pytest -q autodev/tests; \
	fi

# Local-simple GUI/API smoke lane (NXT-007).
smoke-local-simple-e2e:
	$(PYTHON) scripts/local_simple_e2e_smoke.py --artifacts-dir ./artifacts/local-simple-e2e-smoke

# Autonomous E2E smoke lane (AV2-013).
smoke-autonomous-e2e:
	$(PYTHON) scripts/autonomous_e2e_smoke.py --artifacts-dir ./artifacts/autonomous-e2e-smoke

# Fast local CI-equivalent pass for quick iteration.
ci-fast: compile check-fast tests-fast check-status-hooks

# Docs-as-code sanity checks (local markdown links in docs and GitHub templates).
check-docs:
	$(PYTHON) scripts/check_markdown_links.py

# Status-hook docs drift gate (AV4-002).
check-status-hooks:
	$(PYTHON) scripts/status_board_automation.py $(STATUS_HOOK_EVENT) --drift-check

# Strict local CI-equivalent pass (existing behavior + docs gate).
ci-strict: compile check-strict tests-strict check-template check-locks check-docs check-status-hooks

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

# Before/after perf compare report (default repeat=2).
perf-compare:
	$(PYTHON) docs/ops/perf_compare_report.py --repeat 2

# Quick smoke for perf compare script behavior validation.
perf-compare-smoke:
	$(PYTHON) docs/ops/perf_compare_report.py --smoke

# Generate local demo-day scorecard markdown/json artifacts.
demo-scorecard:
	$(PYTHON) scripts/demo_scorecard.py --runs-root ./generated_runs --output-dir ./artifacts/demo-day --latest 5

# One-command demo bootstrap sanity lane (seed fixtures + launch local-simple + health checks).
demo-bootstrap:
	bash scripts/demo_bootstrap.sh

# Keep local-simple running after bootstrap sanity checks.
demo-bootstrap-serve:
	bash scripts/demo_bootstrap.sh --serve --open

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

# Autonomous v2/v3 release evidence gate (default tolerant mode for developer lanes).
check-release-autonomous:
	$(PYTHON) scripts/check_release_autonomous.py --artifacts-dir ./artifacts/autonomous-e2e-smoke

# Strict schema gate for protected lanes (main/nightly).
check-release-autonomous-strict:
	$(PYTHON) scripts/check_release_autonomous.py --strict-schema --artifacts-dir ./artifacts/autonomous-e2e-smoke

release-check: ci-strict check-untyped-defs check-release-gates check-release-autonomous
check-release: release-check

# Explicit aliases for local workflows.
fast: ci-fast
strict: check-release
