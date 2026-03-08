# Autonomous Failure Drill Pack (AV4-008)

Repeatable control-path drills for operator playbook readiness.

Primary playbook: `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`

## Scope

This pack provides **4 deterministic drills** that exercise the main control/policy paths used by autonomous triage:

- quality gate failure path
- stop-guard intervention path
- preflight policy failure path
- budget-guard policy failure path

Each drill maps to a typed code already emitted by autonomous artifacts and linked by operator guidance.

## Drill scenarios (control/policy code mapping)

| Drill ID | Path | Typed code under drill | Playbook anchor | Repeatable command |
|---|---|---|---|---|
| DP-01 | Gate control path | `tests.min_pass_rate_not_met` | `#gate-failures` | `python3 -m pytest -q autodev/tests/test_autonomous_mode.py::test_autonomous_quality_gate_failure_triggers_retry_and_records_typed_reason` |
| DP-02 | Guard control path | `autonomous_guard.repeated_gate_failure_limit_reached` | `#guard-stops` | `python3 -m pytest -q autodev/tests/test_autonomous_mode.py::test_autonomous_stop_guard_repeated_gate_failure_triggers_early_stop_and_persists_artifacts` |
| DP-03 | Preflight policy path | `autonomous_preflight.path_blocked` | `#preflight-failures` | `python3 -m pytest -q autodev/tests/test_autonomous_mode.py::test_autonomous_start_preflight_fails_early_on_blocked_path` |
| DP-04 | Budget policy path | `autonomous_budget_guard.max_autonomous_iterations_reached` | `#budget-guard-stops` | `python3 -m pytest -q autodev/tests/test_autonomous_summary.py::test_extract_autonomous_summary_surfaces_budget_guard_outcome` |

## Operator drill procedure

1. Run one drill command from repo root.
2. Confirm test passes and the asserted typed code matches the scenario row.
3. Open `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md` and validate the referenced section contains actionable steps.
4. Record evidence in the dry-run table below.

## Dry-run evidence (local)

Executed: 2026-03-09 (Asia/Seoul)

| Command batch | Result |
|---|---|
| `python3 -m pytest -q autodev/tests/test_autonomous_mode.py::test_autonomous_quality_gate_failure_triggers_retry_and_records_typed_reason autodev/tests/test_autonomous_mode.py::test_autonomous_stop_guard_repeated_gate_failure_triggers_early_stop_and_persists_artifacts autodev/tests/test_autonomous_mode.py::test_autonomous_start_preflight_fails_early_on_blocked_path autodev/tests/test_autonomous_summary.py::test_extract_autonomous_summary_surfaces_budget_guard_outcome` | `4 passed in 0.19s` |

## Maintenance notes

- Keep drill scenarios mapped only to typed codes with stable operator guidance links.
- If a code changes, update both this drill pack and `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md` in the same PR.
- Preferred lane for drill verification: focused pytest selectors above + `make check-docs`.
