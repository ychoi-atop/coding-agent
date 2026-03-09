# Autonomous failure taxonomy refresh (AV5-008)

Status: Drafted for AV5 kickoff  
Canonical schema: `docs/ops/autonomous_failure_taxonomy_v2.schema.json`  
Canonical example: `docs/ops/autonomous_failure_taxonomy_v2.example.json`

This document refreshes the AV5 failure taxonomy around a strict retryability split:

- **retryable** failures default to bounded automation/manual remediation lanes depending on confidence.
- **non_retryable** failures skip blind replay and route to operator intervention lanes.

## Remediation lanes

- `auto_fix` — deterministic remediation can run automatically (bounded + auditable)
- `manual` — operator performs focused remediation, then resumes
- `escalate` — immediate escalation/approval required before any further run attempts

## Top failure classes mapped to remediation lane

| Class ID | Retryability | Default lane | Typical code family | Operator intent |
|---|---|---|---|---|
| `quality_gate_transient` | `retryable` | `auto_fix` | `tests.*`, `performance.baseline_regression_detected` | Apply bounded fix/retry loop and verify improvement |
| `quality_gate_deterministic` | `non_retryable` | `manual` | `security.*`, deterministic `tests.*` regressions | Patch root cause first; no blind replay |
| `preflight_policy_violation` | `non_retryable` | `manual` | `autonomous_preflight.*` | Correct environment/policy blockers and rerun preflight |
| `guard_control_stop` | `non_retryable` | `escalate` | `autonomous_guard.*` | Treat as hard intervention boundary |
| `budget_guard_stop` | `non_retryable` | `escalate` | `autonomous_budget_guard.*` | Re-scope objective or explicitly approve budget change |
| `tooling_runtime_flake` | `retryable` | `auto_fix` | `runtime.*`, `tooling.*` transient failures | Retry with bounded backoff and telemetry capture |

## Decision policy (summary)

1. Resolve failure class from typed code family.
2. Apply class retryability (`retryable` vs `non_retryable`).
3. Route to default remediation lane (`auto_fix` / `manual` / `escalate`).
4. Preserve lane decision in run artifacts and operator summary.

## Validation gates

- Taxonomy fixture/schema validation: `python scripts/check_failure_taxonomy_v2.py`
- Drill dry-run artifact generation: `python scripts/failure_taxonomy_drill_dry_run.py`
- Docs lane: `make check-docs`

## Drill dry-run evidence (local)

Executed: 2026-03-09 (Asia/Seoul)

| Command | Result |
|---|---|
| `python3 scripts/failure_taxonomy_drill_dry_run.py --artifacts-dir ./artifacts/failure-taxonomy-drill-dry-run` | `PASS` (`artifacts/failure-taxonomy-drill-dry-run/20260309-024347`) |

## Related runbook

- Primary operator runbook: `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
- Deterministic drill scenarios: `docs/AUTONOMOUS_FAILURE_DRILL_PACK.md`
