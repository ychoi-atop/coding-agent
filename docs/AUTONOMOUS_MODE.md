# Autonomous Mode (v1)

`autodev autonomous start` runs AutoDev in unattended mode for real project loops:

1. ingest goal/spec (`--prd`)
2. generate/refresh plan
3. execute implementation tasks
4. self-verify (validators/test/lint via existing pipeline)
5. auto-fix retry loop (bounded)
6. emit final report artifacts

This is a **v1 practical slice**: stable CLI loop + policy boundaries + resumable state.

For commercial rollout governance (quality/release gates, recovery playbooks, KPI/roadmap), see `docs/AUTONOMOUS_COMMERCIAL_PLAN.md`.

Update (v1b, 2026-03-07): link and rollout-governance references were refreshed to keep autonomous-mode operators aligned with the commercial delivery plan.

---

## Command

```bash
autodev autonomous start \
  --prd examples/PRD.md \
  --out ./generated_runs \
  --profile enterprise
```

### Key options

- `--max-iterations <N>`: total unattended attempts (first run + retries)
- `--time-budget-sec <seconds>`: hard wall-clock budget
- `--workspace-allowlist <path>` (repeatable): allowed roots for PRD/config/output/run
- `--blocked-paths <path>` (repeatable): hard-deny roots
- `--allow-docker-build`: opt-in docker build execution (default: blocked)
- `--allow-external-side-effects`: explicit flag for future higher-risk external actions (default: false)
- `--preflight-check-artifact-writable`: opt-in preflight probe to verify `.autodev/` artifact path is writable
- `--resume`: first attempt starts with normal run checkpoint resume behavior
- `--resume-state --run-dir <existing_run>`: continue a prior autonomous session from saved state (with deterministic recovery for partial/corrupt state artifacts)

### Status helper

```bash
autodev autonomous status --run-dir ./generated_runs/<run_id>
```

Prints `.autodev/autonomous_state.json`.

### Summary helper

```bash
autodev autonomous summary --run-dir ./generated_runs/<run_id>
# optional human-readable format
autodev autonomous summary --run-dir ./generated_runs/<run_id> --format text
```

Default output is machine-readable JSON with latest run status, preflight status/reason codes,
gate pass/fail counts, dominant gate fail codes, latest auto-fix strategy, and stop-guard
decision fields (with graceful warnings for missing artifacts).

---

## Policy profile (config)

Configure defaults in `config.yaml`:

```yaml
run:
  autonomous:
    max_iterations: 3
    time_budget_sec: 3600
    workspace_allowlist:
      - "/path/to/projects"
    blocked_paths:
      - "/path/to/projects/secrets"
    external_side_effects:
      allow_docker_build: false
      allow_external_side_effects: false
    quality_gate_policy:
      tests:
        min_pass_rate: 0.9
      security:
        max_high_findings: 0
      performance:
        max_regression_pct: 5
    preflight:
      check_artifact_writable: false
    stop_guard_policy:
      max_consecutive_gate_failures: 3
      max_consecutive_no_improvement: 2
      rollback_recommendation_enabled: true
```

Notes:
- CLI flags override config values.
- Default side-effect posture is safe (`false`).
- `allow_docker_build=false` forces docker-build validation tasks to remain disabled in autonomous mode.
- `quality_gate_policy` actively evaluates tests/security/performance gates at each autonomous iteration end using available signals.
- Autonomous mode persists gate trends in `.autodev/autonomous_gate_baseline.json` (recent observed values per gate) and uses the baseline to strengthen regression judgment (especially performance placeholder signals).
- When a gate fails, autonomous mode records typed fail reasons in attempt artifacts and enters bounded `auto_fix_retry` (still constrained by `max_iterations` and `time_budget_sec`).
- Auto-fix retries now route through a gate-code/category strategy map (`tests-focused`, `security-focused`, `perf-focused`, `mixed`) and persist selected strategy + rationale per iteration.
- Strategy selection uses a bounded no-improvement heuristic to avoid repeating identical strategies when the prior same-strategy retry did not measurably reduce gate failures.
- Stop-guard policy (`stop_guard_policy`) adds deterministic early-stop decisions before exhausting wasteful retries when (a) gate failures repeat consecutively or (b) consecutive gate-failed attempts show no measurable improvement.
- Guard decisions persist typed reason codes (for example `autonomous_guard.repeated_gate_failure_limit_reached`) and optional rollback recommendation markers across state/report/summary artifacts.
- Autonomous mode now runs a preflight safety gate before the unattended loop starts (path allowlist/blocked checks + required readable file checks, with optional artifact writability probe).
- Preflight failures stop early with typed reason codes (for example `autonomous_preflight.path_blocked`) and persist diagnostics in state/report/summary artifacts.
- `--resume-state` now performs deterministic state normalization (attempt de-duplication + `current_iteration` alignment) to prevent duplicate/lost attempt indexing across restart boundaries.
- Resume/recovery paths emit typed `resume_diagnostics` entries in state/report/summary outputs so operators can see when corrupt/partial artifacts were auto-recovered.
- Gate fail reasons include a normalized taxonomy payload (`taxonomy_version`, `category`, `severity`, `retryable`, `signal_source`) and explicit baseline regression codes (e.g. `performance.baseline_regression_detected`) so downstream report/triage logic can branch deterministically.

---

## Resumable state and artifacts

Each autonomous run writes:

- `.autodev/autonomous_state.json` — live state machine snapshot (phase/status/attempts)
- `.autodev/autonomous_report.json` — machine-readable final report (includes latest `gate_results` when configured)
- `.autodev/autonomous_gate_results.json` — per-iteration quality gate evaluation history
- `.autodev/autonomous_gate_baseline.json` — persistent recent gate observations used for trend-aware regression checks
- `.autodev/autonomous_strategy_trace.json` — per-iteration strategy routing/rotation trace with latest selected strategy
- `.autodev/autonomous_guard_decisions.json` — stop-guard decision history with typed reason codes and rollback recommendation markers
- `AUTONOMOUS_REPORT.md` — quick human summary
- existing run artifacts (`report.json`, quality artifacts, checkpoints) are preserved

Terminal conditions:
- `completed`
- `failed` (`preflight_failed`, `max_iterations_exceeded`, or `time_budget_exceeded`)

---

## Backward compatibility

- Existing `autodev --prd ...` flow is unchanged.
- Existing `autodev gui` / `autodev local-simple` flow is unchanged.
- Autonomous mode wraps the same core run engine (`run_autodev_enterprise`) and validator pipeline.

---

## Current v1 limits

- No dedicated GUI control surface yet (state artifacts + CLI status are provided now).
- External side-effect guard currently focuses on default-safe toggles and docker-build blocking.
- Future extension points: richer side-effect policy gates (network/publish/git push), pause/resume control endpoints, and GUI cards.
