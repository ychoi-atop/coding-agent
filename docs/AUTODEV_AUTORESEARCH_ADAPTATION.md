# AUTODEV × `karpathy/autoresearch` — Adaptation Proposal (AV5-Aligned)

Status: Draft (for AV5 governance evaluation)

## 1) Purpose and scope

### Why this doc exists

Define a bounded, operator-trustworthy way to borrow useful autonomy patterns from `karpathy/autoresearch` for autodev, without importing research-agent assumptions that conflict with AV5 governance.

### What to borrow

- **Tight autonomous loop shape:** propose → mutate → evaluate → keep/discard.
- **Objective acceptance criteria:** keep changes only when measurable gates pass.
- **Small, iterative deltas:** prefer narrow slices over large unreviewable jumps.
- **Auditability:** preserve reproducible evidence for each autonomous decision.

### What not to copy

- **Open-ended exploration loops** with unclear stop conditions.
- **Unbounded tool freedom** across many mutation surfaces in one iteration.
- **Subjective “looks better” acceptance** without deterministic checks.
- **Research-speed-first defaults** that bypass autodev safety and docs parity controls.

---

## 2) Core principles adapted to autodev

1. **Single mutation surface (per loop)**
   - One loop may mutate **exactly one declared target surface** (e.g., one file family, one doc pack, or one scoped code unit).
   - Cross-surface edits require a new loop with a new declared surface.

2. **Fixed loop budget**
   - Each autonomous run has a predeclared budget: `max_loops`, `max_minutes`, `max_failures`.
   - Budget exhaustion is a hard stop (no silent extension).

3. **Objective accept gate**
   - A mutation is accepted only if deterministic gates pass (lint/tests/docs/status-hook drift checks as applicable).
   - Failed gate => discard or quarantine candidate and continue only if budget remains.

4. **Keep/discard discipline**
   - Every candidate ends in one of two states: **KEEP** (merged into working branch) or **DISCARD** (reverted/abandoned with reason).
   - No hidden “partially kept” state.

5. **Policy-as-code**
   - Loop constraints, pass thresholds, and escalation rules must be machine-enforced configuration, not operator memory.
   - Human overrides require explicit audit note and reason code.

---

## 3) Proposed autonomous loop v1 (bounded)

`Loop v1` is a deterministic, stage-bounded cycle for autodev:

1. **Initialize run contract**
   - Declare objective, mutation surface, budget, mandatory checks, acceptance thresholds.
2. **Generate candidate change**
   - Produce smallest viable diff that could improve objective metric.
3. **Local validation pre-gate**
   - Run fast local checks (format/lint/static checks if relevant).
4. **Acceptance gate**
   - Run required objective checks (for docs: `make check-docs`; for code slices: scoped tests + policy checks).
5. **Score candidate**
   - Compute weighted score (quality/safety/speed) + pass/fail gates.
6. **Decision**
   - If hard gates pass and weighted score ≥ threshold: **KEEP**.
   - Else: **DISCARD** (or park as evidence-only artifact).
7. **Record evidence**
   - Append run ledger row: candidate id, checks, scores, keep/discard reason, timing.
8. **Budget check**
   - Continue next iteration only if budget remains and no stop/escalation trigger fired.
9. **Finalize**
   - Emit summary packet with kept diffs, discarded attempts, metrics, and escalation notes.

Bounded defaults (v1 suggestion):
- `max_loops: 5`
- `max_minutes: 30`
- `max_failures: 2` (hard-gate failures)
- `max_consecutive_discards: 3`

---

## 4) Acceptance scoring rubric

## 4.1 Hard pass/fail gates (must all pass)

- **Safety gate:** no policy violation, no prohibited mutation surface, no secret/material exposure.
- **Determinism gate:** required checks complete with reproducible command output.
- **Scope gate:** diff stays inside declared mutation surface.

If any hard gate fails → candidate is **DISCARD**.

## 4.2 Weighted score (0–100)

`Total = Quality(50) + Safety(30) + Speed(20)`

| Dimension | Weight | Example signals |
|---|---:|---|
| Quality | 50 | check pass rate, spec/doc conformance, clarity/completeness delta |
| Safety | 30 | zero policy exceptions, clean guardrail checks, no scope drift |
| Speed | 20 | cycle time vs budget, retries needed, operator intervention count |

Recommended threshold:
- **KEEP requires**: hard gates pass **and** `Total >= 75`
- **Promote without human review** (future option): `Total >= 85` and zero warnings

---

## 5) Guardrails and stop/escalation criteria

### Guardrails

- Only pre-approved tools/commands for declared surface.
- No force-push, no history rewrite, no destructive cleanup during autonomous loop.
- One branch per objective; no multi-objective mixing in same run.
- Every loop writes machine-readable audit row.

### Stop criteria (immediate)

- Hard safety gate failure.
- Mutation escapes declared surface.
- Budget exhausted (`loops` or `minutes`).

### Escalation criteria (operator review required)

- Two consecutive hard-gate failures.
- Three consecutive discards with no measurable score improvement.
- Conflicting signals (e.g., quality improves but safety degrades).
- Required check unavailable/flaky beyond retry budget.

---

## 6) Rollout plan (3 phases)

| Phase | Scope | Duration (target) | Success metrics |
|---|---|---|---|
| Phase 1 — Shadow | Run loop in evidence-only mode (no auto-keep) on docs/governance slices | 1 week | ≥90% deterministic check completion, audit rows present for 100% loops |
| Phase 2 — Guarded Keep | Allow auto-KEEP for low-risk surfaces (docs + narrow config) with strict thresholds | 1–2 weeks | KEEP precision ≥85%, escalation rate <20%, zero policy incidents |
| Phase 3 — Controlled Expansion | Expand to selected code surfaces with scoped tests and tighter stop rules | 2+ weeks | Median cycle time improvement ≥20%, no increase in incident rate, rollback <10% |

Exit rule per phase: advance only if metrics hold for at least one full reporting window.

---

## 7) Example CLI/operator workflow (status-hook + docs checks aligned)

```bash
# 0) clean base
git fetch origin
git checkout main
git pull --ff-only origin main

# 1) create objective branch
git checkout -b auto/objective-<id>

# 2) validate governance registry/state before loop
python3 scripts/status_board_automation.py --validate-registry
python3 scripts/status_board_automation.py --detect-event

# 3) run bounded loop (pseudocode)
autodev loop run \
  --objective "<goal>" \
  --mutation-surface "docs" \
  --max-loops 5 \
  --max-minutes 30 \
  --max-failures 2 \
  --accept-threshold 75

# 4) mandatory docs parity gate for docs surfaces
make check-docs

# 5) drift/no-drift verification (if status docs touched)
python3 scripts/status_board_automation.py <event> --drift-check

# 6) commit only kept diff + evidence pointer
git add docs/ artifacts/status-hooks/
git commit -m "autodev: keep candidate <id> (score=<n>, gates=pass)"
git push -u origin auto/objective-<id>
```

Operator pseudocode for decision point:

```text
for candidate in loop:
  run_hard_gates()
  if hard_gate_fail: discard(candidate); maybe_escalate(); continue

  score = weighted_score(candidate)
  if score >= threshold:
    keep(candidate)
  else:
    discard(candidate)

  if stop_or_escalate_triggered:
    break
```

---

## 8) Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Metric gaming (optimize score, not outcome) | False-positive KEEP decisions | Keep hard gates non-negotiable; periodically recalibrate rubric with human-reviewed samples |
| Check flakiness | Random discard/escalation noise | Retry budget + flaky-test quarantine lane; separate infra failure from candidate failure |
| Scope creep across surfaces | Audit/safety drift | Enforce mutation-surface allowlist + fail-fast scope gate |
| Over-conservative thresholds | Low throughput | Phase-based threshold tuning with explicit precision/recall tracking |
| Under-conservative thresholds | Safety regressions | Start in shadow mode; require zero-incident window before expansion |
| Operator blind spots | Missed systemic drift | Weekly ledger review + trend dashboard (discard reasons, escalation frequency, gate failures) |

---

## Suggested next actions

1. Add loop-policy schema (budget/gates/thresholds/escalation) under policy-as-code.
2. Implement ledger emitter for keep/discard evidence rows.
3. Pilot Phase 1 on AV5 docs-only slices and report metrics in next wave checkpoint.
