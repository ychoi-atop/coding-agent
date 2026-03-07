# BACKLOG — Next Week (Operator Reliability)

This backlog is the execution companion for `docs/PLAN_NEXT_WEEK.md`.

## NXT wave closure snapshot (NXT-001 ~ NXT-014)

- Closure state: ✅ **complete**
- Ticket count: **14 / 14 done**
- Mainline merge span: PR **#7 → #21**
- Post-closure operating mode: stabilization window + hotfix-only unless blocker emerges

## Newly completed

### NXT-013 — Next-cut RC checklist + changelog draft ✅ done

- **Goal:** Prepare docs/process artifacts for release-candidate dry-run and cut handoff.
- **Scope:** docs-only updates (no product code changes).
- **Completion evidence:**
  - `docs/RC_NEXT_CUT_CHECKLIST.md`
  - `docs/CHANGELOG_DRAFT_NEXT_CUT.md`

### NXT-014 — Backlog grooming from demo findings + priority re-rank ✅ done

- **Goal:** Convert demo follow-up work into prioritized, implementation-ready next-wave tickets.
- **Scope:** docs/planning only (no product code changes).
- **Completion evidence:**
  - `docs/PLAN_NEXT_WEEK.md` (priority stack update)
  - `docs/BACKLOG_NEXT_WEEK.md` (actionable ticket metadata)

## Priority-ranked carry-over tickets (actionable)

> Ticket format for all carry-over items
> - **Priority:** P0 / P1 / P2
> - **Owner role:** backend / frontend / platform / docs
> - **Effort:** S / M / L
> - **Acceptance criteria:** testable outcomes
> - **PR split:** recommended patch boundaries for reviewability

### NXT-015 — RC evidence completeness preflight

- **Priority:** P0
- **Owner role:** platform
- **Effort:** S
- **Scope:** Add a lightweight preflight script/check that blocks RC GO when checklist evidence fields remain placeholders.
- **Acceptance criteria:**
  - Preflight flags unresolved placeholder markers (`TODO`, empty paths, unchecked pass/fail blocks) in `docs/RC_NEXT_CUT_CHECKLIST.md`.
  - Command returns non-zero on missing required evidence and prints actionable fix hints.
  - Preflight usage is documented in RC checklist and README release flow section.
- **PR split:**
  1) Script + unit test/smoke check
  2) RC checklist/README docs wiring

### NXT-016 — Local-simple startup diagnostics quick-check lane

- **Priority:** P0
- **Owner role:** platform
- **Effort:** M
- **Scope:** Reduce demo-time startup failures by adding a one-command diagnostics lane for host/port/dependency/API sanity.
- **Acceptance criteria:**
  - One command validates Python/make/curl, port availability, and essential endpoints (`/healthz`, `/api/runs`, `/api/gui/context`).
  - Failures map to short recovery guidance (port conflict, missing fixtures, server not up).
  - `docs/DEMO_PLAYBOOK.md` pre-demo checklist points to this lane.
- **PR split:**
  1) Diagnostics command/script
  2) Demo playbook + local-simple docs integration

### NXT-017 — Processes triage UX follow-up (filtering + stale hints)

- **Priority:** P1
- **Owner role:** frontend
- **Effort:** M
- **Scope:** Improve operator recovery speed in the Processes tab during stop/retry troubleshooting.
- **Acceptance criteria:**
  - Processes list supports failed/running/stale quick filters without full page reload.
  - Detail view highlights most recent transition + stale-age hint when updates are old.
  - Empty/error states remain explicit and include next action hints.
- **PR split:**
  1) Frontend state/filter UX
  2) Copy/docs updates for process troubleshooting flow

### NXT-018 — Artifact Viewer triage exports + docs consistency lint

- **Priority:** P1
- **Owner role:** backend
- **Effort:** M
- **Scope:** Strengthen triage handoff by making artifact exports easier and keeping known-limits statements in sync across docs.
- **Acceptance criteria:**
  - Artifact Viewer API/UX supports reliable copy/download path for failed-validator payload handoff.
  - Add docs consistency check for known-limits statements between README and local-simple/runbook docs.
  - `make check-docs` (or companion docs check) fails on drift with clear diff/hint output.
- **PR split:**
  1) Artifact handoff improvement
  2) Docs consistency checker + docs updates

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
- NXT-011 ✅ local-simple operator runbook refresh
- NXT-012 ✅ explicit empty/error/loading UX pass for Overview/Validation/Processes
- NXT-013 ✅ next-cut RC checklist + changelog draft
- NXT-014 ✅ backlog grooming + priority re-rank from demo findings

## Related docs

- `docs/PLAN_NEXT_WEEK.md`
- `docs/RELEASE_NOTES_SHOWOFF_LOCAL_V2.md`
- `docs/STABILIZATION_MODE.md`
- `docs/LOCAL_SIMPLE_MODE.md`
- `docs/DEMO_PLAYBOOK.md`
- `docs/RC_NEXT_CUT_CHECKLIST.md`
- `docs/CHANGELOG_DRAFT_NEXT_CUT.md`
- `README.md`
