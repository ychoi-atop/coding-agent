# AUTONOMOUS V6 — Scoring Threshold Matrix (AV6-002)

Status: ✅ Drafted for merge (deterministic policy baseline)
Owner: @autonomous-docs
Last updated: 2026-03-09 (Asia/Seoul)

## Purpose

Define deterministic score-to-action semantics for Autonomous V6 quality gates.
This matrix is intentionally docs-first and backward-compatible: it does not change existing stage contracts, and is consumable by CLI/API/GUI/operator runbooks.

## Inputs

- `quality_score` (`0.00` ~ `1.00`, inclusive)
- `hard_blocker` (`true|false`) — from AV6 blocker policy contract
- `blocker_severity` (`critical|high|medium|low|none`)
- `attempt_index` (`1..N`, current attempt number)
- `max_retries` (default `2`, unless stage contract overrides)

## Precedence order (deterministic)

1. **Hard-blocker override first**
   - `hard_blocker=true` and severity `critical|high` ⇒ `STOP` (ignore score band).
   - `hard_blocker=true` and severity `medium|low` ⇒ at least `ESCALATE` (score cannot downgrade).
2. **Score band decision** from the matrix below.
3. **Tie-break rules** if score is missing/ambiguous or equal to boundary values.
4. **Fallback semantics** when required inputs are unavailable.

## Score threshold bands

| Score range (`quality_score`) | Baseline action | Notes |
|---|---|---|
| `0.90` ~ `1.00` | `PASS` | High confidence output; proceed to next stage/closure checks. |
| `0.75` ~ `<0.90` | `RETRY` | Candidate is close; run bounded retry within retry budget. |
| `0.50` ~ `<0.75` | `ESCALATE` | Requires operator review or stronger model/tooling path. |
| `0.00` ~ `<0.50` | `STOP` | Low quality; fail fast and emit incident-style rationale. |

## Deterministic decision table

| hard_blocker | blocker_severity | score band result | retries remaining (`attempt_index <= max_retries`) | final action |
|---|---|---|---|---|
| true | critical/high | any | any | `STOP` |
| true | medium/low | pass/retry/escalate | any | `ESCALATE` |
| false | none | pass | any | `PASS` |
| false | none | retry | true | `RETRY` |
| false | none | retry | false | `ESCALATE` |
| false | none | escalate | any | `ESCALATE` |
| false | none | stop | any | `STOP` |

## Tie-break rules

1. **Boundary-inclusive policy**
   - `0.90`, `0.75`, and `0.50` belong to the higher-confidence band listed above (e.g., `0.75` ⇒ `RETRY`).
2. **Multiple scorers disagreement**
   - Use `min(score_i)` (conservative floor) for the action decision.
3. **Rounding discipline**
   - Normalize scores to two decimals (`round-half-away-from-zero`) before applying bands.
4. **Action tie between `RETRY` and `ESCALATE` from mixed signals**
   - Prefer stricter action: `ESCALATE`.
5. **Action tie between `ESCALATE` and `STOP`**
   - Prefer stricter action: `STOP`.

## Fallback semantics

If one or more required inputs are missing/corrupt:

1. Missing `quality_score` ⇒ `ESCALATE` (reason: `score_unavailable`).
2. Non-numeric/out-of-range `quality_score` ⇒ `STOP` (reason: `score_invalid`).
3. Missing blocker metadata while blocker subsystem reported active ⇒ `ESCALATE` (reason: `blocker_metadata_missing`).
4. Missing retry counters (`attempt_index`/`max_retries`) ⇒ assume no retries remaining and resolve to `ESCALATE` for retry band.

## Operator-facing rationale fields (minimum)

Every decision should emit at least:

- `decision_action` (`pass|retry|escalate|stop`)
- `decision_reason` (canonical reason code)
- `quality_score_normalized`
- `hard_blocker`
- `blocker_severity`
- `attempt_index`
- `max_retries`
- `threshold_policy_version` (`av6-002-v1`)

## Examples

1. `quality_score=0.92`, no blocker ⇒ `PASS`
2. `quality_score=0.78`, no blocker, attempt 1/2 ⇒ `RETRY`
3. `quality_score=0.78`, no blocker, attempt 3/2 ⇒ `ESCALATE`
4. `quality_score=0.95`, blocker=`true`, severity=`high` ⇒ `STOP`
5. score missing, blocker absent ⇒ `ESCALATE` (`score_unavailable`)

## Related docs

- `docs/AUTONOMOUS_V6_BACKLOG.md`
- `docs/AUTONOMOUS_V6_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V6_WAVE_CHECKPOINT.md`
- `docs/AUTONOMOUS_FAILURE_TAXONOMY_V2.md`
- `docs/AUTONOMOUS_RETRY_STRATEGY_V2.md`
