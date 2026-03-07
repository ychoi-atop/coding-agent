# STABILIZATION MODE — SHOWOFF LOCAL V2

Status: closed on 2026-03-07 21:33 KST after Day-1/2/3 clean runs (`NXT-001` ~ `NXT-014`).

## Goal

Run a short observation window to validate release quality before resuming feature work.

- Duration: **2–3 days**
- Default policy: **hotfix-only**
- Exit criteria: no unresolved P0/P1 regressions affecting local-simple operator flows

## Guardrails

1. No net-new product features during stabilization.
2. Only the following change types are allowed:
   - blocker/critical bug fixes (P0/P1)
   - release/docs metadata corrections
   - test fixes required to restore CI truthfulness
3. Any non-hotfix item is deferred to post-stabilization backlog (`NXT-015+`).

## Daily observation checklist (operator)

Run this once per day during the window:

1. `make check-docs`
2. `make smoke-local-simple-e2e`
3. Basic API sanity:
   - `GET /healthz`
   - `GET /api/runs`
   - `GET /api/gui/context`
4. Manual GUI spot-check:
   - run create/start (dry-run path where applicable)
   - process status transitions visible
   - artifact viewer loads known large JSON sample
5. Record findings in the PR thread (or release tracking issue) with date/time and pass/fail.

## Hotfix policy

### Severity gates

- **P0 (blocker):** data loss, inability to demo core operator path, run controls fundamentally broken
- **P1 (high):** high-frequency failure in core workflow with clear workaround cost
- **P2/P3:** defer unless explicitly approved by release owner

### Hotfix workflow

1. Branch from `main` using `hotfix/<short-name>`.
2. Keep patch minimal and reversible.
3. Include focused validation evidence (test output + repro/verification note).
4. Open PR with label/title prefix: `hotfix:`.
5. Merge only after reviewer approval and passing required checks.

## Optional release tag instructions

> Optional: create tags only when auth/permissions are available. Do **not** force tag push if unavailable.

```bash
# from updated main
 git checkout main
 git pull --ff-only origin main

# lightweight tag (example)
 git tag showoff-local-v2

# or annotated tag (recommended)
 git tag -a showoff-local-v2 -m "Showoff Local V2 snapshot (NXT-001..NXT-014)"

# push tag (optional)
 git push origin showoff-local-v2
```

If tag push fails due to auth/permission:
- keep local tag for later, or
- ask an authorized maintainer to push the tag.

## Exit criteria (end of stabilization)

All must be true:

- No open P0/P1 regressions related to local-simple operator flows.
- `make check-docs` passes on `main`.
- Smoke lane (`make smoke-local-simple-e2e`) passes on latest `main` baseline.
- Release notes and operator checklist are up to date.

## Closure note (2026-03-07 21:33 KST)

Stabilization is formally complete.

- Day-1/2/3 reports are all green (`docs/STABILIZATION_DAY1_REPORT.md`, `docs/STABILIZATION_DAY2_REPORT.md`, `docs/STABILIZATION_DAY3_REPORT.md`).
- No open P0/P1 hotfixes remain from the stabilization window.
- Change-control policy returns from hotfix-only to normal feature delivery.

## Post-exit actions / handoff to next-wave planning

1. Announce stabilization completion in PR/release thread.
2. Resume planned next-wave work in priority order:
   - NXT-015
   - NXT-016
   - NXT-017
   - NXT-018
3. Convert any deferred stabilization observations into explicit backlog tickets.
4. Use planning sources to launch next wave: `docs/PLAN_NEXT_WEEK.md` and `docs/BACKLOG_NEXT_WEEK.md`.

## Related docs

- `docs/STATUS_BOARD_CURRENT.md`
- `docs/STABILIZATION_48H_CHECKLIST.md`
- `docs/RELEASE_NOTES_SHOWOFF_LOCAL_V2.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/RC_NEXT_CUT_CHECKLIST.md`
