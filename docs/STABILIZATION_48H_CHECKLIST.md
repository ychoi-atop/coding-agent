# STABILIZATION 48H CHECKLIST

Use this runbook for the first 48 hours after NXT wave closure.

## Operating policy

- **Mode:** Stabilization
- **Duration:** 48 hours
- **Change policy:** Hotfix-only (P0/P1 bug fixes + required docs/test corrections)
- **Out of scope:** Net-new features, refactors without incident tie-in, broad behavior changes

## Daily checks (run once per day, minimum)

> Target order: docs integrity → smoke confidence → runtime health → process/error signal.

### 1) Documentation integrity

```bash
make check-docs
```

Pass criteria:
- No broken local markdown links in docs / templates.

### 2) Smoke confidence (local-simple critical path)

```bash
make smoke-local-simple-e2e
```

Pass criteria:
- Lane succeeds end-to-end.
- Artifacts captured under `artifacts/local-simple-e2e-smoke/<timestamp>/`.

### 3) Service health/API sanity

Run and verify:
- `GET /healthz`
- `GET /api/runs`
- `GET /api/gui/context`

Pass criteria:
- Health endpoint returns healthy status.
- Core API endpoints respond without server error.

### 4) Scorecard check

Run and verify:
- `GET /api/scorecard/latest` (or Overview scorecard widget)

Pass criteria:
- Latest scorecard is retrievable.
- No unexpected regression signal vs previous baseline.

### 5) Process/error monitoring

Run and verify:
- `GET /api/processes`
- Backend/server logs for unhandled exceptions, retry loops, repeated 5xx

Pass criteria:
- No stuck process transitions.
- No repeating high-severity runtime errors.

### 6) Record evidence

For each day, log in PR/release thread:
- Timestamp (KST)
- Checks run (1~5)
- Pass/fail summary
- Any incident + severity + action

## Incident severity routing

### Severity definitions

- **P0 (Blocker):** demo/operator core path unusable, data loss/corruption risk, or system-down behavior.
- **P1 (High):** frequent critical path failure with costly workaround.
- **P2 (Medium):** non-critical degradation with viable workaround.
- **P3 (Low):** minor defect/cosmetic issue.

### Routing and SLA expectations

| Severity | Routing | Expected action |
|---|---|---|
| P0 | Release owner + on-call reviewer immediately | Stop non-essential work, open hotfix branch now, patch/verify first |
| P1 | Release owner same day | Prioritize hotfix in stabilization window |
| P2 | Backlog owner | Defer unless explicitly escalated by release owner |
| P3 | Backlog owner | Defer to post-stabilization queue |

## Hotfix-only policy (enforcement)

1. Branch naming: `hotfix/<short-name>`
2. Patch size: smallest possible, reversible
3. Required proof in PR:
   - Repro steps (before)
   - Validation evidence (after)
   - Affected scope and rollback note
4. Merge gate:
   - Reviewer approval
   - Required checks green
   - No unrelated changes in diff

## Exit criteria (end stabilization)

All conditions must be true:

- No open **P0/P1** incidents tied to local-simple operator path.
- `make check-docs` passes on latest `main`.
- `make smoke-local-simple-e2e` passes on latest `main`.
- `/healthz`, `/api/runs`, `/api/gui/context`, `/api/scorecard/latest`, `/api/processes` all healthy in latest check.
- Status board and handoff docs are updated for next phase.

## Exit actions

1. Announce stabilization complete in tracking PR/thread.
2. Re-open normal feature intake.
3. Move deferred P2/P3 issues into next-wave backlog with priority tags.

## Related docs

- `docs/STATUS_BOARD_CURRENT.md`
- `docs/STABILIZATION_MODE.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
