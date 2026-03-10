# PLAN — Next Wave (AV6 Kickoff Active)

## Scope

This plan tracks active AV6 kickoff after AV5 checkpoint capture.
Primary objective is to operationalize existing autoresearch integration safely with deterministic blockers, thresholding, and runtime budgets.

## Current state snapshot

- AV2 wave (`AV2-001` ~ `AV2-014`) is complete and merged.
- AV3 wave (`AV3-001` ~ `AV3-013`) is complete and merged.
- AV4 wave (`AV4-001` ~ `AV4-014`) is complete and closed on `main`.
- AV5 wave is checkpointed on `main` (`docs/AUTONOMOUS_V5_WAVE_PLAN.md`, `docs/AUTONOMOUS_V5_BACKLOG.md`, `docs/AUTONOMOUS_V5_WAVE_CHECKPOINT.md`).
- AV6 kickoff package is started (`docs/AUTONOMOUS_V6_WAVE_PLAN.md`, `docs/AUTONOMOUS_V6_BACKLOG.md`).
- Active status-hook event/state: `av6.kickoff.started`.

## AV6 kickoff execution plan

1. **Publish canonical AV6 wave docs:** keep plan/backlog/status/README links in sync.
2. **Lock hard safety blockers:** document non-negotiable stop/escalate lanes for autoresearch-triggered actions.
3. **Define deterministic scoring and budget gates:** establish threshold matrix + stage/run time-budget contracts.
4. **Attach observability baseline:** ensure blocker/threshold/budget decisions are traceable in operator evidence.

## Workflow confidence checks

- `make smoke-autonomous-e2e`
- `make check-release-autonomous`
- `make check-docs`
- `make check-status-hooks` (canonical status-hook drift gate)

Manual status-hook fallback:
- If event integration is unavailable, run `python3 scripts/status_board_automation.py <event_id>` to sync status/plan/backlog/closure docs from the canonical event map.
- CI drift check equivalent: `python3 scripts/status_board_automation.py <event_id> --drift-check`.
- Event-to-transition reference: `docs/STATUS_HOOK_TRANSITION_MATRIX.md`.

## Definition of done (AV6 kickoff)

- AV6 kickoff state is visible across status/plan/backlog/README docs.
- AV6 plan and prioritized backlog are published with explicit DoD/test/PR-split fields.
- Docs/status automation stays drift-free (`make check-status-hooks`, `make check-docs`).

## Related docs

- `docs/AUTONOMOUS_V6_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V6_BACKLOG.md`
- `docs/AUTONOMOUS_V6_WAVE_CHECKPOINT.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V5_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V5_BACKLOG.md`
- `docs/AUTONOMOUS_V5_WAVE_CHECKPOINT.md`
- `docs/STATUS_HOOK_TRANSITION_MATRIX.md`
