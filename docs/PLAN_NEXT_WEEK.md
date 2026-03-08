# PLAN — Next Wave (AV4 Kickoff Active)

## Scope

This plan tracks kickoff execution after AV3 closure.
Primary objective is to start AV4 delivery with compact PR slices while preserving AV3 reliability safeguards.

## Current state snapshot

- AV2 wave (`AV2-001` ~ `AV2-014`) is complete and merged.
- AV3 wave (`AV3-001` ~ `AV3-013`) is complete and merged.
- AV4 kickoff package is now active (`docs/AUTONOMOUS_V4_WAVE_PLAN.md`, `docs/AUTONOMOUS_V4_BACKLOG.md`).

## AV4 kickoff execution plan

1. **Close carryover first:** land `AV4-001` (AV3-014 carryover) and baseline retention controls.
2. **Ship operator signal surfaces:** deliver concise audit summaries across API/GUI/CLI.
3. **Automate docs lifecycle:** reduce manual wave-boundary drift via rollup automation.
4. **Hold quality gates:** keep deterministic smoke/release/docs checks continuously green.

## Workflow confidence checks

- `make smoke-autonomous-e2e`
- `make check-release-autonomous`
- `make check-docs`

## Definition of done (AV4 kickoff package)

- AV4 plan/backlog are the active source of truth.
- Status/plan/backlog docs explicitly show AV4 kickoff started.
- README/docs navigation includes AV4 links.
- Docs validation remains green (`make check-docs`).

## Related docs

- `docs/AUTONOMOUS_V4_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V4_BACKLOG.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`
- `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
