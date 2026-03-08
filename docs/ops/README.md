# docs/ops

This folder contains SDLC/운영 governance scripts and checks used by `coding-agent`:

- `check_template_parity_audit.py`
- `check_template_ci_drift.sh`
- `check_template_dependency_locks.sh`
- `run_template_tests.sh`
- `checklist.md`
- `AUTONOMOUS_V2_RELEASE_CHECKLIST.md` (autonomous v2 release readiness + rollout guardrails)
- `quality-gate-checklist.md` (Spec-first + Test-first + Docs-as-code quality gates)
- `benchmark_generate_cycle.py` (baseline vs optimized generate timing smoke)
- `perf_validation.py` (validator timing extraction + regression comparison)
- `perf_smoke.py` (collect current run perf to `.autodev/perf.json`)
- `perf_strict.py` (perf regression gate against previous `.autodev/perf.json`)

Use `make` for standard lanes:

```bash
make fast        # fast local loop: compile + ruff + unit tests
make strict      # release-ready lane: mypy + tests + contract checks + release gates

# explicit targets
make ci-fast
make ci
make benchmark-generate
make perf-smoke      # collect lightweight performance telemetry from generated run
make perf-strict     # compare against prior `.autodev/perf.json` with conservative thresholds
make check-docs      # verify local markdown links in docs and .github templates
make check-release-autonomous  # verify autonomous smoke evidence for release gate
```
