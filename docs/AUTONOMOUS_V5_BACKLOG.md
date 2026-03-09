# AUTONOMOUS V5 — Prioritized Backlog (Checkpoint)

Status: 🚧 Active (checkpoint captured 2026-03-09)
Companion plan: `docs/AUTONOMOUS_V5_WAVE_PLAN.md`
Checkpoint: `docs/AUTONOMOUS_V5_WAVE_CHECKPOINT.md`

| ID | Priority | Effort | Status | Ticket | Definition of Done (DoD) | Test plan | PR split |
|---|---|---:|---|---|---|---|---|
| AV5-001 | P0 | S | ✅ Merged | AV5 kickoff packet publish (plan/backlog/status sync) | AV5 plan/backlog exist and status/plan/backlog/README docs all reference kickoff started state | `make check-docs` + link/path verification | 1 PR |
| AV5-002 | P0 | S | ✅ Merged | AV5 status-hook transition design draft | AV5 kickoff/execution/stabilization/closure transition map documented with event IDs and expected doc deltas | docs diff review + `make check-status-hooks` (no regression) | 1 PR |
| AV5-003 | P0 | M | ✅ Merged | Stage-boundary contract spec (ingest/plan/execute/verify) | each stage has required inputs/outputs/failure semantics documented in canonical schema/spec docs | schema/examples validation + docs check | 1 PR |
| AV5-004 | P0 | M | ✅ Merged | Retry strategy semantics v2 (deterministic replay policy) | retry classes and stop/escalate thresholds defined with deterministic examples | policy unit tests + replay smoke scenario | 1 PR |
| AV5-005 | P1 | S | ✅ Merged | Incident packet minimal-field contract v2 | incident packet required fields reduced to operator-actionable minimum with rationale | packet schema tests + fixture snapshots | 1 PR |
| AV5-006 | P1 | M | ✅ Merged | Operator summary parity map (CLI/API/GUI) | parity matrix for summary fields and error states documented and linked from runbook | CLI/API snapshot tests + GUI smoke checklist | 1 PR |
| AV5-007 | P1 | S | ✅ Merged | AV5 residual-risk log template | reusable AV5 risk log template published with owner/severity/mitigation sections | template render check + docs check | 1 PR |
| AV5-008 | P1 | M | ✅ Merged | Failure taxonomy refresh (retryable vs non-retryable) | top failure classes mapped to remediation lane (auto-fix/manual/escalate) | taxonomy fixture tests + drill dry-run | 1 PR |
| AV5-009 | P1 | S | ✅ Merged | Closure evidence checklist scaffold (AV5) | AV5 closure checklist template added with explicit evidence artifact pointers | docs lint + checklist completeness review | 1 PR |
| AV5-010 | P2 | S | ⏸️ Deferred / not started | Docs cross-link upgrade for AV5 governance set | README + planning docs include AV5 plan/backlog links; no stale “active AV4 kickoff” labels | `make check-docs` + grep audit for stale links | 1 PR |
| AV5-011 | P2 | M | ⏸️ Deferred / not started | Transition-runbook update (AV4 closed → AV5 active) | runbook includes canonical transition steps and fallback command flow | runbook walkthrough + command dry-run | 1 PR |
| AV5-012 | P2 | S | ✅ Merged | Backlog schema lint extension for AV5 IDs | docs validation enforces AV5 ticket row format (id/priority/effort/DoD/test/PR split) | docs schema lint test + negative cases | 1 PR |
| AV5-013 | P2 | M | ⏸️ Deferred / not started | Smoke evidence index for AV5 kickoff period | kickoff smoke artifacts indexed with timestamp/source/check outcome table | smoke script run + index freshness check | 1 PR |
| AV5-014 | P2 | S | ✅ Merged | AV5 carryover policy definition | explicit rule for deferring AV5 tickets to AV6 with closure annotation format | docs review + sample carryover entry check | 1 PR |

## Checkpoint notes

- P0 and P1 kickoff slices (`AV5-001` ~ `AV5-009`) are merged on `main`.
- Remaining P2 governance items (`AV5-010` ~ `AV5-013`) are held as next-wave candidates, not active kickoff blockers.

## Related docs

- `docs/AUTONOMOUS_V5_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V5_WAVE_CHECKPOINT.md`
- `docs/AUTONOMOUS_V5_CARRYOVER_POLICY.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`
