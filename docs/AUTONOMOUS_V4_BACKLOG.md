# AUTONOMOUS V4 — Prioritized Backlog (Kickoff)

Status: 🚧 Kickoff started (2026-03-08)
Companion plan: `docs/AUTONOMOUS_V4_WAVE_PLAN.md`

| ID | Priority | Effort | Ticket | Definition of Done (DoD) | Test plan | PR split |
|---|---|---:|---|---|---|---|
| AV4-001 | P0 | S | AV3-014 carryover: status board automation hooks | status transitions auto-updated from canonical events; manual fallback documented | unit for hook mapping + docs check | 1 PR |
| AV4-002 | P0 | M | Timeline retention classes (hot/warm/archive) | retention classes configurable + default policy documented | policy parser/unit + sample retention smoke | 1 PR |
| AV4-003 | P0 | M | Artifact compaction pipeline for timeline/audit logs | compaction job preserves replay-critical fields + index integrity | golden snapshot diff + replay smoke | 2 PRs (core + smoke/docs) |
| AV4-004 | P0 | S | Retention safety guardrails in preflight | dangerous retention config blocked with actionable error | config validation tests | 1 PR |
| AV4-005 | P1 | M | Operator audit summary API | API returns concise state/risk/action summary with typed schema | API contract tests + fixture snapshots | 1 PR |
| AV4-006 | P1 | M | GUI audit summary panel | panel renders API summary with empty/error/loading states | UI component tests + manual smoke checklist | 1 PR |
| AV4-007 | P1 | S | CLI summary command for operator triage | CLI prints same canonical summary fields as API | CLI snapshot tests | 1 PR |
| AV4-008 | P1 | S | Failure playbook drill pack (control-path scenarios) | at least 4 repeatable drill scenarios mapped to control/policy codes | drill dry-run evidence + docs check | 1 PR |
| AV4-009 | P1 | S | Incident packet enrichment for retention decisions | packet includes retention/compaction decisions + rationale links | packet schema tests + sample export | 1 PR |
| AV4-010 | P2 | M | Docs rollup automation (status/plan/backlog/closure) | wave-boundary docs can be updated by one scripted flow | script unit tests + docs lint | 2 PRs (script + adoption) |
| AV4-011 | P2 | S | Cross-doc link integrity check enhancement | AV4 docs links validated in `make check-docs` path | broken-link regression test | 1 PR |
| AV4-012 | P2 | S | Backlog metadata normalization (priority/effort schema) | backlog entries follow strict table schema + validation | docs schema lint test | 1 PR |
| AV4-013 | P2 | S | Runbook update for retention/compaction ops | operator runbook updated with recovery path and rollback steps | runbook checklist walk-through | 1 PR |
| AV4-014 | P2 | M | AV4 kickoff closure evidence bundle template | reusable checklist/template for AV4 closeout packet | template render check + docs check | 1 PR |

## Prioritization notes

- Execute `AV4-001` ~ `AV4-004` first to de-risk automation and data lifecycle.
- Keep API/GUI/CLI summary parity (`AV4-005`~`AV4-007`) in the same milestone window.
- Treat docs automation (`AV4-010`+) as leverage work only after P0/P1 signal quality is stable.

## Related docs

- `docs/AUTONOMOUS_V4_WAVE_PLAN.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/AUTONOMOUS_V3_WAVE_CLOSURE.md`
