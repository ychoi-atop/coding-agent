# BACKLOG — Next Wave (AV5 Kickoff Queue)

This queue is the execution companion for `docs/PLAN_NEXT_WEEK.md`.
Current mode is AV5 kickoff active with AV4 closure preserved.

## Wave baseline

- AV2 closure: ✅ `AV2-001` ~ `AV2-014`
- AV3 closure: ✅ `AV3-001` ~ `AV3-013`
- AV4 closure: ✅ complete (`AV4-001` ~ `AV4-014`)
- AV5 kickoff: 🚧 started (`docs/AUTONOMOUS_V5_WAVE_PLAN.md`, `docs/AUTONOMOUS_V5_BACKLOG.md`)
- Active status-hook event/state: `av5.kickoff.started`

## AV5 kickoff top queue

| ID | Priority | Effort | Ticket |
|---|---|---|---|
| AV5-001 | P0 | S | Publish AV5 kickoff packet across status/plan/backlog/README |
| AV5-002 | P0 | S | Draft AV5 status-hook transition map (kickoff→execution→stabilization→closure) |
| AV5-003 | P0 | M | Define deterministic stage-boundary contracts |
| AV5-004 | P0 | M | Finalize retry semantics v2 with explicit stop/escalate lanes |
| AV5-005 | P1 | S | Normalize incident packet minimal fields for operator action |
| AV5-006 | P1 | M | Lock CLI/API/GUI summary parity map |

## Notes

- AV5 full ticket ledger is managed in `docs/AUTONOMOUS_V5_BACKLOG.md`.
- Keep PR slices narrow and evidence explicit per ticket.

## Related docs

- `docs/AUTONOMOUS_V5_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V5_BACKLOG.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`
- `docs/STATUS_HOOK_TRANSITION_MATRIX.md`
