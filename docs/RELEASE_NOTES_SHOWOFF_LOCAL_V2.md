# RELEASE NOTES — SHOWOFF LOCAL V2 (Snapshot)

Status: Release snapshot for the close of NXT wave (`NXT-001` ~ `NXT-014`).

## Summary

This cut closes the current operator-reliability wave and prepares the repo for a short stabilization window before the next implementation wave.

- Wave closure: ✅ `NXT-001` ~ `NXT-014` complete on `main`
- Focus: local-simple operator reliability, demo continuity, RC readiness
- Scope balance:
  - Product/runtime improvements: `NXT-001` ~ `NXT-012`
  - Docs/release governance updates: `NXT-013` ~ `NXT-014`

## Included pull requests (NXT wave)

- #7 — NXT-001: quick-run payload validation hardening
- #8 — NXT-002: process polling backoff + stale indicator
- #9 — NXT-003: artifact viewer large-JSON responsiveness
- #10 — NXT-004: timeline taxonomy normalization
- #11 — NXT-006: correlation-id tracing for run controls
- #12 — NXT-005: scorecard API + overview widget
- #13 — NXT-008: fixture expansion + typed artifact errors
- #14 — NXT-009: stop/retry race hardening + idempotent retry
- #15 — NXT-007: local-simple E2E smoke lane
- #16 — NXT-009 test follow-up: duplicate stop/retry idempotency coverage
- #17 — NXT-010: one-command demo bootstrap lane
- #18 — NXT-011: local-simple operator runbook refresh
- #19 — NXT-012: explicit empty/error/loading UX pass
- #20 — NXT-013: RC checklist + changelog draft
- #21 — NXT-014: backlog grooming + priority re-rank

## Feature and workflow highlights

1. **Operator-path hardening**
   - Start/stop/retry flow resilience improved.
   - Polling/backoff behavior and stale indicators clarified.
   - Correlation-id tracing added for run-control diagnostics.

2. **GUI usability for demo and triage**
   - Overview scorecard now available via API + widget.
   - Artifact viewer remains responsive for large JSON.
   - Explicit empty/error/loading states reduce operator ambiguity.

3. **Validation and test confidence**
   - Local-simple E2E smoke lane in place.
   - Fixture coverage expanded and artifact-shape errors typed.
   - Stop/retry idempotency paths covered by tests.

4. **Operator docs and release discipline**
   - Local-simple runbook updated.
   - RC checklist and changelog draft established.
   - Planning/backlog docs aligned for the next-wave queue.

## What is not included

- No new WebSocket/live-stream telemetry.
- No hardened multi-user mode changes (`autodev gui` hardening is still separate from local-simple mode).
- No schema versioning for JSON artifacts in this wave.

## Next step after this snapshot

Move into a **2–3 day stabilization mode** using `docs/STABILIZATION_MODE.md`, with hotfix-only changes allowed during observation unless blocker-level risk is discovered.

## Related docs

- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STABILIZATION_MODE.md`
- `docs/RC_NEXT_CUT_CHECKLIST.md`
- `docs/CHANGELOG_DRAFT_NEXT_CUT.md`
