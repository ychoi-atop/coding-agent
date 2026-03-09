# Autonomous stage-boundary contract (AV5-003)

Status: Drafted for AV5 kickoff
Canonical schema: `docs/ops/autonomous_stage_boundary_contract.schema.json`
Canonical example: `docs/ops/autonomous_stage_boundary_contract.example.json`

This document defines the deterministic stage-boundary contract for AV5 autonomous runs.
Each stage must satisfy required inputs/outputs and failure semantics before the run may transition.

## Stages (ordered)

1. `ingest`
2. `plan`
3. `execute`
4. `verify`

A run **must not skip or reorder** stages.

## Stage contracts

### 1) ingest

- **Required inputs**
  - `run_context`
  - `operator_intent`
  - `source_locator`
- **Required outputs**
  - `ingest_snapshot_id`
  - `normalized_inputs`
  - `ingest_summary`
- **Failure semantics**
  - `retry_class`: `retryable`
  - `stop_condition`: source/auth/config validation fails repeatedly
  - `escalate_condition`: retries exhausted or source contract mismatch detected
  - `evidence_required`: `ingest_error_digest`, `source_ref`

### 2) plan

- **Required inputs**
  - `normalized_inputs`
  - `policy_bundle`
  - `execution_constraints`
- **Required outputs**
  - `plan_id`
  - `action_graph`
  - `risk_register`
  - `approval_packet`
- **Failure semantics**
  - `retry_class`: `conditional`
  - `stop_condition`: plan cannot satisfy policy/constraint invariants
  - `escalate_condition`: repeated planning dead-end or policy conflict unresolved
  - `evidence_required`: `plan_validation_report`, `constraint_diff`

### 3) execute

- **Required inputs**
  - `plan_id`
  - `action_graph`
  - `approval_token`
- **Required outputs**
  - `execution_log`
  - `artifact_manifest`
  - `result_snapshot`
- **Failure semantics**
  - `retry_class`: `conditional`
  - `stop_condition`: non-retryable tool/runtime failure class triggered
  - `escalate_condition`: partial side-effects require operator remediation
  - `evidence_required`: `failure_class`, `step_trace`, `artifact_refs`

### 4) verify

- **Required inputs**
  - `result_snapshot`
  - `artifact_manifest`
  - `acceptance_criteria`
- **Required outputs**
  - `verification_report`
  - `decision` (`pass`|`retry`|`escalate`)
  - `operator_summary`
- **Failure semantics**
  - `retry_class`: `non_retryable`
  - `stop_condition`: verification evidence is incomplete or criteria mismatch persists
  - `escalate_condition`: unable to prove acceptance after bounded replay
  - `evidence_required`: `assertion_results`, `decision_rationale`

## Transition invariants

- Stage output fields are the next stage's admissible input source.
- `approval_token` is mandatory for `execute`; missing token is a hard stop.
- `verify.decision=retry` is only valid when retry policy allows deterministic replay.
- `verify.decision=escalate` must include evidence pointers in `operator_summary`.

## Validation gates

- Schema + examples validation: `python scripts/check_stage_boundary_contract.py`
- Docs lane: `make check-docs`
