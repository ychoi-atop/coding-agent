# AUTONOMOUS V5 — Wave Plan (Kickoff)

Status: 🚧 Kickoff started (2026-03-09)

## Goals / outcomes

1. **Deterministic autonomous execution:** reduce run-to-run variance with tighter stage contracts and replayable control decisions.
2. **Operator trust at handoff boundaries:** make autonomous outputs easier to approve/escalate with concise evidence bundles and clearer state transitions.
3. **Lower incident recovery time:** improve failure classification and remediation guidance so failed runs can be retried or escalated quickly.
4. **Sustainable delivery cadence:** preserve AV4 reliability posture while shipping AV5 in narrow, docs/test-first PR slices.

## 2-week milestone slices

### Week 1 (Days 1-7): kickoff foundations + control contracts
- Publish AV5 kickoff packet (plan + backlog + status/README linkage).
- Define AV5 control-surface deltas (stage boundary contracts, retry policy semantics, event/status expectations).
- Lock first P0 slice boundaries with explicit DoD/test lanes.
- Keep quality gates green (`make check-docs`, `make check-status-hooks`, smoke/release checks).

### Week 2 (Days 8-14): operator-facing hardening + rollout readiness
- Land first operator-facing AV5 slices (incident guidance/summary parity paths).
- Add AV5 closure-readiness checklist stubs and evidence mapping.
- Validate docs/status drift-free operation after AV5 kickoff transitions.
- Produce residual-risk snapshot and next-slice rollout order.

## Architecture deltas from AV4

- **Control contracts:** AV4 emphasized status hooks + retention policy; AV5 adds stricter stage-level acceptance contracts for deterministic retries/replays.
- **Decision traceability:** AV4 added concise summaries; AV5 expands to explicit remediation-oriented evidence lanes for retry vs escalate decisions.
- **Wave governance:** AV4 closed with full lifecycle automation; AV5 starts from docs-first kickoff with narrower PR split discipline and explicit closure criteria from day 1.
- **Handoff ergonomics:** AV4 improved operator visibility; AV5 focuses on reducing cognitive load at incident/handoff points via compact, canonical packets.

## Top risks + mitigations

1. **Risk:** AV5 introduces contract complexity that slows delivery.
   - **Mitigation:** enforce small PR slices, mark non-goals per ticket, and keep each ticket independently reviewable.
2. **Risk:** New control semantics drift from existing status-hook automation.
   - **Mitigation:** maintain compatibility checks, document transitional state mapping, gate with docs/status drift checks.
3. **Risk:** Operator surfaces become verbose instead of actionable.
   - **Mitigation:** define minimal required fields, prefer summary-first rendering, and validate with focused smoke scenarios.
4. **Risk:** Kickoff backlog overcommits beyond 2-week capacity.
   - **Mitigation:** priority guardrails (P0/P1 first), explicit effort caps, and carryover rules for P2 work.

## Related docs

- `docs/AUTONOMOUS_V5_BACKLOG.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`
- `docs/STATUS_HOOK_TRANSITION_MATRIX.md`
- `docs/AUTONOMOUS_STAGE_BOUNDARY_CONTRACT.md`
- `docs/templates/AV5_CLOSURE_EVIDENCE_CHECKLIST.md.tmpl`
- `docs/templates/AV5_RESIDUAL_RISK_LOG.md.tmpl`
