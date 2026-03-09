# PLAN — Next Wave (AV5 Kickoff Active)

## Scope

This plan tracks active AV5 kickoff after AV4 closure.
Primary objective is to execute AV5 kickoff foundations while preserving AV4 reliability and documentation discipline.

## Current state snapshot

- AV2 wave (`AV2-001` ~ `AV2-014`) is complete and merged.
- AV3 wave (`AV3-001` ~ `AV3-013`) is complete and merged.
- AV4 wave (`AV4-001` ~ `AV4-014`) is complete and closed on `main`.
- AV5 kickoff package is started (`docs/AUTONOMOUS_V5_WAVE_PLAN.md`, `docs/AUTONOMOUS_V5_BACKLOG.md`).
- Active status-hook event/state: `av5.kickoff.started`.

## AV5 kickoff execution plan

1. **Publish canonical AV5 wave docs:** keep plan/backlog/status/README links in sync.
2. **Lock deterministic kickoff boundaries:** define first P0 slices with strict DoD/test lanes.
3. **Preserve quality baseline:** keep smoke/release/docs/status-hook drift checks continuously green.
4. **Prepare week-2 rollout:** sequence operator-facing and governance slices without enlarging PR scope.

## Workflow confidence checks

- `make smoke-autonomous-e2e`
- `make check-release-autonomous`
- `make check-docs`
- `make check-status-hooks` (canonical status-hook drift gate)

Manual status-hook fallback:
- If event integration is unavailable, run `python3 scripts/status_board_automation.py <event_id>` to sync status/plan/backlog/closure docs from the canonical event map.
- CI drift check equivalent: `python3 scripts/status_board_automation.py <event_id> --drift-check`.
- Event-to-transition reference: `docs/STATUS_HOOK_TRANSITION_MATRIX.md`.

## Definition of done (AV5 kickoff)

- AV5 kickoff state is visible across status/plan/backlog/README docs.
- AV5 plan and prioritized backlog are published with explicit DoD/test/PR-split fields.
- Docs/status automation stays drift-free (`make check-status-hooks`, `make check-docs`).

## Related docs

- `docs/AUTONOMOUS_V5_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V5_BACKLOG.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`
- `docs/STATUS_HOOK_TRANSITION_MATRIX.md`
- `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
