# AUTONOMOUS V4 — Wave Plan (Kickoff)

Status: 🚧 Kickoff started (2026-03-08)

## Goals / outcomes

1. **Operational continuity at scale:** make long-running autonomous sessions sustainable via retention/compaction and clearer run-state automation.
2. **Operator decision speed:** reduce time-to-triage with higher-signal summary surfaces and drill-ready playbooks.
3. **Docs/process reliability:** reduce wave-boundary manual drift by tightening docs automation and cross-link integrity.
4. **Safe iterative delivery:** keep AV3 reliability posture while shipping AV4 through small, reviewable slices.

## 2-week milestone slices

### Week 1 (Days 1-7): foundation + carryover close
- Land AV3 carryover (`AV4-001`: status board automation hooks).
- Ship baseline retention/compaction policy contract and guardrails.
- Define AV4 observability summary schema + API/UI integration plan.
- Keep check gates green (`check-docs`, autonomous smoke/release evidence).

### Week 2 (Days 8-14): operator-facing value + hardening
- Deliver operator audit summary view and drill workflow docs.
- Implement docs rollup automation for wave status/closure transitions.
- Validate AV4 controls via deterministic smoke updates + playbook drills.
- Close kickoff package with release-readiness evidence and residual-risk log.

## Architecture deltas from AV3

- **State automation:** AV3 relied on manual status doc transitions; AV4 introduces hook-based status propagation.
- **Artifact lifecycle:** AV3 emitted timeline/audit artifacts without explicit retention policy; AV4 adds retention classes, compaction, and recovery constraints.
- **Operator surface:** AV3 established parity surfaces; AV4 adds concise audit dashboards optimized for active incident triage.
- **Process tooling:** AV3 depended on manual docs sync; AV4 adds docs rollup automation for wave lifecycle docs.

## Top risks + mitigations

1. **Risk:** Retention/compaction causes loss of critical forensic context.
   - **Mitigation:** preserve canonical checkpoints + audit index, enforce no-loss fields, add replay validation.
2. **Risk:** Automation hooks desynchronize source-of-truth docs.
   - **Mitigation:** single-writer contract, idempotent updates, docs integrity check in CI.
3. **Risk:** New summary dashboards increase operator ambiguity.
   - **Mitigation:** strict field semantics, drill scenarios, and playbook-mapped UI copy.
4. **Risk:** AV4 scope creep reintroduces large PRs and review latency.
   - **Mitigation:** ticket-level PR splits, effort caps, and explicit DoD/test-plan per ticket.

## Related docs

- `docs/AUTONOMOUS_V4_BACKLOG.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`
- `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
