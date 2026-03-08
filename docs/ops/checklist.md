# Template and Release SDLC Checklist

## 1) Required Checks (machine-checkable)

- `docs/ops/template-validation-contract.json`
- `docs/ops/check_template_parity_audit.py`
- `docs/ops/check_template_dependency_locks.sh`
- `docs/ops/check_template_ci_drift.sh`

Run from repo root:

```bash
bash docs/ops/check_template_parity_audit.sh
bash docs/ops/check_template_dependency_locks.sh
bash docs/ops/check_template_ci_drift.sh .
```

Required pre-merge checks:

```bash
make check-template
make check-locks
```

## 2) Lane defaults for faster iteration vs release strictness

- Fast path (local loop): `make fast`  
  - `compile` + `ruff` + `autodev/tests`
  - Use when iterating daily and validating syntax + light behavior.

- Strict path (release): `make strict` (alias for `make check-release`)  
  - `compile + mypy + full tests + template/parity checks + lock checks + release gates`
  - Use before merge and release prep.

Target matrix:

```bash
make fast              # fast local lane
make strict            # full release lane
make ci                # same as ci-strict (legacy)
make ci-fast
make ci-strict
make check-release      # same as strict
```

## 2.1) Benchmark lane

```bash
make benchmark-generate
make perf-smoke
make perf-strict
```

- `benchmark-generate`: baseline vs optimized generation timing smoke with `docs/ops/benchmark_generate_cycle.py`.
- `perf-smoke`: parses latest `.autodev/task_quality_index.json` (or task quality snapshots), persists `generated_dir/.autodev/perf.json`, and prints summary.
- `perf-strict`: compares `perf.json` against previous run, failing on conservative regressions.

## 2.2) Smoke confidence lanes

```bash
make smoke-local-simple-e2e
make smoke-autonomous-e2e
make check-release-autonomous
```

- `smoke-local-simple-e2e`: GUI/API local-simple critical-path smoke.
- `smoke-autonomous-e2e`: deterministic autonomous end-to-end smoke (preflight → gate fail eval → guard stop → summary CLI → `/api/autonomous/quality-gate/latest`).
- `check-release-autonomous`: validates required autonomous release evidence signals (preflight/gate/guard/summary/API smoke) from the latest smoke artifact.

## 3) Release-readiness (Go/No-Go)

### Owners
- **Release Lead**: final go/no-go decision, release branch readiness
- **Platform**: runs `make ci` and verifies CI artifact freshness
- **QA**: validates regression scope and template test coverage
- **Security**: validates lock/scan outputs and policy violations
- **Docs**: verifies docs/reference consistency (Onboarding/Deployment/Monitoring/Failure docs)

### Go/No-Go Criteria
1. `make ci` must pass
2. `make check-template` must pass (workflow parity drift free)
3. `make check-locks` must pass (requirements vs lock parity)
4. `docs/ops/check_template_parity_audit.py` pass criteria: workflow/docs references are in sync
5. `make check-release-autonomous` must pass (autonomous v2 smoke evidence complete)
6. Known-risk items must have mitigation notes in this checklist

### Weekly cadence
- **Mon**: lightweight run `make compile`, `make check`
- **Wed/Thu**: run `make tests`
- **Fri**: run `make ci` + release-readiness review and update backlog

## 4) Performance tuning knobs to try next

- Lower strictness in iterative loops:
  - swap `make ci` → `make ci-fast`
  - move to strict only before merging
- Reduce expensive validator load in benchmark lane by adjusting
  - `--optimized-validators`
  - `--optimized-max-fix-loops`
  - `--optimized-max-fix-loops-per-task`
- If slowdowns persist:
  - trim generated PRD surface area for smoke checks
  - pin smaller local models for warm-up runs
  - add provider health warmup before timing windows

## 5) Periodic template governance

From root:

```bash
make check-template
make check-locks
make check-untyped-defs
```

If any command fails repeatedly, stop and file an explicit corrective ticket before new feature merges.
