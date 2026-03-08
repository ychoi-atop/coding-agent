# Status Hook Transition Matrix (AV4)

Canonical status-hook events drive deterministic updates for:
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`

| Event | STATUS_BOARD_CURRENT.md | PLAN_NEXT_WEEK.md | BACKLOG_NEXT_WEEK.md |
|---|---|---|---|
| `av4.kickoff.started` | Mode `AV4 Kickoff`; AV4 snapshot `🚧 Kickoff started (plan + backlog published)` | Title `# PLAN — Next Wave (AV4 Kickoff Active)`; snapshot `- AV4 kickoff package is now active (...)` | Title `# BACKLOG — Next Wave (AV4 Kickoff Queue)`; baseline `- AV4 kickoff: 🚧 started` |
| `av4.execution.in_progress` | Mode `AV4 Execution`; AV4 snapshot `🏗️ Execution in progress (P0 slices actively shipping)` | Title `# PLAN — Next Wave (AV4 Execution In Progress)`; snapshot `- AV4 execution is in progress (...)` | Title `# BACKLOG — Next Wave (AV4 Active Delivery Queue)`; baseline `- AV4 execution: 🏗️ in progress` |
| `av4.stabilization.started` | Mode `AV4 Stabilization`; AV4 snapshot `🧪 Stabilization started (smoke + release gates in focus)` | Title `# PLAN — Next Wave (AV4 Stabilization Active)`; snapshot `- AV4 stabilization is active (...)` | Title `# BACKLOG — Next Wave (AV4 Stabilization Queue)`; baseline `- AV4 stabilization: 🧪 started` |
| `av4.closed` | Mode `AV4 Closed`; AV4 snapshot `✅ Closed (execution + stabilization complete)` | Title `# PLAN — Next Wave (Post-AV4 Planning)`; snapshot `- AV4 is closed on \`main\`; planning focus shifts to the next wave package.` | Title `# BACKLOG — Next Wave (Post-AV4 Intake Queue)`; baseline `- AV4 closure: ✅ complete` |

## Notes

- Use `python3 scripts/status_board_automation.py --validate-registry` to verify schema/registry integrity.
- Use `python3 scripts/status_board_automation.py <event> --drift-check` to enforce no-doc-drift in CI.
- Apply/drift-check/replay append audit entries to `artifacts/status-hooks/status-hook-audit.jsonl` (override with `--audit-log`).
- Replay prior entries by id/index: `python3 scripts/status_board_automation.py --replay <entry_id|index>` (safe dry-run by default).
- Use `--apply` with replay only when explicit write-back is intended.
