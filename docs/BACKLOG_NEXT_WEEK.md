# BACKLOG — Next Week (Operator Reliability)

This backlog is the execution companion for `docs/PLAN_NEXT_WEEK.md`.

## In progress / ready

### NXT-011 — Local-simple operator runbook refresh

- **Goal:** Align README + docs with current post-NXT-010 workflow.
- **Scope:** docs-only updates (no product code changes).
- **Acceptance:**
  - local-simple commands are copy-paste ready
  - stale/contradictory instructions removed
  - planning links updated to next-week docs
  - minimal docs validation command documented/used (`make check-docs`)

## Completed baseline (for context)

- NXT-001 ✅ quick-run payload validation hardening
- NXT-002 ✅ process polling backoff + stale indicator
- NXT-003 ✅ artifact viewer large-JSON responsiveness
- NXT-004 ✅ timeline taxonomy normalization
- NXT-005 ✅ scorecard API + Overview widget
- NXT-006 ✅ correlation-id tracing for run controls
- NXT-007 ✅ local-simple E2E smoke lane
- NXT-008 ✅ fixture expansion + typed artifact errors
- NXT-009 ✅ stop/retry race hardening + idempotent retry
- NXT-010 ✅ one-command demo bootstrap lane

## Candidate follow-ups (post NXT-011)

- Tighten demo failure-playbook examples with scripted fixture fault injection.
- Add lightweight docs freshness checks for known-limit statements across README/docs.
- Add explicit "operator day-1" quickstart command block in one canonical location and link to it from all runbooks.

## Related docs

- `docs/PLAN_NEXT_WEEK.md`
- `docs/LOCAL_SIMPLE_MODE.md`
- `docs/DEMO_PLAYBOOK.md`
- `README.md`
