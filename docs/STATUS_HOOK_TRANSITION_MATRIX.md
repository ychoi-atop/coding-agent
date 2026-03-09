# Status Hook Transition Matrix (AV4 + AV5 Kickoff)

Canonical status-hook events drive deterministic updates for:
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`

| Event | STATUS_BOARD_CURRENT.md | PLAN_NEXT_WEEK.md | BACKLOG_NEXT_WEEK.md | AUTONOMOUS_V4_WAVE_CLOSURE.md |
|---|---|---|---|---|
| `av4.kickoff.started` | Mode `AV4 Kickoff`; AV4 snapshot `🚧 Kickoff started (plan + backlog published)` | Title `# PLAN — Next Wave (AV4 Kickoff Active)`; snapshot `- AV4 kickoff package is now active (...)` | Title `# BACKLOG — Next Wave (AV4 Kickoff Queue)`; baseline `- AV4 kickoff: 🚧 started` | Status `🚧 Open — closure blocked (pending merge/completion evidence)` |
| `av4.execution.in_progress` | Mode `AV4 Execution`; AV4 snapshot `🏗️ Execution in progress (P0 slices actively shipping)` | Title `# PLAN — Next Wave (AV4 Execution In Progress)`; snapshot `- AV4 execution is in progress (...)` | Title `# BACKLOG — Next Wave (AV4 Active Delivery Queue)`; baseline `- AV4 execution: 🏗️ in progress` | Status `🏗️ Open (execution in progress)` |
| `av4.stabilization.started` | Mode `AV4 Stabilization`; AV4 snapshot `🧪 Stabilization started (smoke + release gates in focus)` | Title `# PLAN — Next Wave (AV4 Stabilization Active)`; snapshot `- AV4 stabilization is active (...)` | Title `# BACKLOG — Next Wave (AV4 Stabilization Queue)`; baseline `- AV4 stabilization: 🧪 started` | Status `🧪 Open (stabilization active)` |
| `av4.closed` | Mode `AV4 Closed`; AV4 snapshot `✅ Closed (execution + stabilization complete)` | Title `# PLAN — Next Wave (Post-AV4 Planning)`; snapshot `- AV4 wave (`AV4-001` ~ `AV4-014`) is complete and closed on \`main\`.` | Title `# BACKLOG — Next Wave (Post-AV4 Intake Queue)`; baseline `- AV4 closure: ✅ complete (`AV4-001` ~ `AV4-014`)` | Status `✅ Closed on \`main\`` |
| `av5.kickoff.started` | Mode `AV5 Kickoff Active`; AV4 snapshot remains `✅ Closed (execution + stabilization complete)` while AV5 kickoff starts | Title `# PLAN — Next Wave (AV5 Kickoff Active)`; snapshot `- AV5 kickoff package is started (...)` | Title `# BACKLOG — Next Wave (AV5 Kickoff Queue)`; baseline `- AV5 kickoff: 🚧 started (...)` | Status `✅ Closed on \`main\`` |

## Notes

- Use `python3 scripts/status_board_automation.py --validate-registry` to verify schema/registry integrity.
- Use `python3 scripts/status_board_automation.py <event> --drift-check` to enforce no-doc-drift in CI.
- Apply/drift-check/replay append audit entries to `artifacts/status-hooks/status-hook-audit.jsonl` (override with `--audit-log`).
- Apply + replay `--apply` acquire a write lock (`artifacts/status-hooks/status-hook-write.lock` by default) to prevent concurrent doc writes.
- Stale lock policy defaults to 900s (`--lock-stale-seconds`), and stale override is explicit via `--force-lock`.
- Replay prior entries by id/index: `python3 scripts/status_board_automation.py --replay <entry_id|index>` (safe dry-run by default).
- Use `--apply` with replay only when explicit write-back is intended.
