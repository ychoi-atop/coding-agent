# PLAN — Next Wave (Post-AV4 Planning)

## Scope

This plan tracks handoff after AV4 closure.
Primary objective is to prepare AV5 intake/scoping while preserving AV4 reliability and documentation discipline.

## Current state snapshot

- AV2 wave (`AV2-001` ~ `AV2-014`) is complete and merged.
- AV3 wave (`AV3-001` ~ `AV3-013`) is complete and merged.
- AV4 wave (`AV4-001` ~ `AV4-014`) is complete and closed on `main`.
- Active status-hook event/state: `av4.closed`.

## Post-AV4 planning execution plan

1. **Open AV5 intake:** define first narrow slice candidates and acceptance boundaries.
2. **Preserve quality baseline:** keep smoke/release/docs/status-hook drift checks continuously green.
3. **Carry forward closure discipline:** require explicit evidence lanes for each AV5 slice.
4. **Publish kickoff packet:** finalize AV5 plan/backlog docs before switching the active wave mode.

## Workflow confidence checks

- `make smoke-autonomous-e2e`
- `make check-release-autonomous`
- `make check-docs`
- `make check-status-hooks` (canonical status-hook drift gate)

Manual status-hook fallback:
- If event integration is unavailable, run `python3 scripts/status_board_automation.py <event_id>` to sync status/plan/backlog/closure docs from the canonical event map.
- CI drift check equivalent: `python3 scripts/status_board_automation.py <event_id> --drift-check`.
- Event-to-transition reference: `docs/STATUS_HOOK_TRANSITION_MATRIX.md`.

## Definition of done (post-AV4 handoff)

- AV4 remains marked closed across status/plan/backlog/closure docs.
- AV5 intake candidates and sequencing are documented.
- Docs/status automation stays drift-free (`make check-status-hooks`, `make check-docs`).

## Related docs

- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STATUS_HOOK_TRANSITION_MATRIX.md`
- `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
