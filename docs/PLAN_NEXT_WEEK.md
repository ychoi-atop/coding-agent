# PLAN — Next Wave (AV4 Kickoff)

## Scope

This plan reflects `main` after AV3 closure.
Primary objective is to execute the AV4 kickoff set with reliability and operator-safety guardrails retained from AV3.

## Current state snapshot

- AV2 wave (`AV2-001` ~ `AV2-014`) is complete and merged.
- AV3 wave (`AV3-001` ~ `AV3-013`) is complete and merged.
- AV3 closure summary: `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`

## AV4 kickoff execution plan

1. **Carryover + hardening:** land AV4 baseline from deferred/next items (`AV3-014` carryover + new AV4 priorities).
2. **Operational confidence:** keep autonomous evidence, docs checks, and GUI/API operator flows stable.
3. **Narrow PR slices:** preserve reviewability with compact, dependency-aware patches.
4. **Risk containment:** keep rollout gated by deterministic checks and playbook-ready recovery paths.

## Workflow confidence checks

- Keep `make smoke-autonomous-e2e` as deterministic gate smoke.
- Keep `make check-release-autonomous` in release-readiness flow.
- Keep `make check-docs` mandatory for docs/process updates.

## Definition of done (AV4 kickoff package)

- AV3 closure is reflected in status/plan/backlog docs.
- AV4 candidate list is prioritized and actionable.
- README/docs navigation includes AV3 closure and AV4 planning links.
- Docs validation remains green (`make check-docs`).

## Related docs

- `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_MODE.md`
- `docs/AUTONOMOUS_FAILURE_PLAYBOOK.md`
- `docs/ops/AUTONOMOUS_V2_RELEASE_CHECKLIST.md`
