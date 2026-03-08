# BACKLOG — Next Wave (AV4 Candidates)

This backlog is the execution companion for `docs/PLAN_NEXT_WEEK.md`.

## Wave closure baseline

- AV2 closure: ✅ `AV2-001` ~ `AV2-014`
- AV3 closure: ✅ `AV3-001` ~ `AV3-013`
- AV3 closure summary: `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`

## AV4 prioritized candidates

| ID | Priority | Effort | Ticket | Notes |
|---|---|---|---|---|
| AV4-001 | P0 | S | AV3-014 carryover: status board automation hooks | Carry forward template automation hooks deferred from AV3 |
| AV4-002 | P0 | M | Autonomous run timeline retention/compaction policy | Prevent artifact growth while preserving operator triage value |
| AV4-003 | P1 | M | Operator control audit dashboard summary | Improve at-a-glance risk/decision visibility for active runs |
| AV4-004 | P1 | S | Failure playbook drill scenarios for AV3 controls | Add repeatable incident drills and expected operator actions |
| AV4-005 | P2 | S | Docs automation for closure/status rollups | Reduce manual drift between wave status docs |

## Prioritization notes

- Start with **AV4-001** to close AV3 carryover and keep status automation consistent.
- Keep PR slices narrow and test/doc evidence explicit for each AV4 ticket.
- Preserve deterministic check lanes before broadening scope.

## Related docs

- `docs/PLAN_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_MODE.md`
- `README.md`
