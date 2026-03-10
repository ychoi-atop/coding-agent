# Status Hook Transition Matrix (AV4 → AV6 Lifecycle)

Canonical status-hook events drive deterministic updates for:
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_V4_WAVE_CLOSURE.md`

| Event | STATUS_BOARD_CURRENT.md | PLAN_NEXT_WEEK.md | BACKLOG_NEXT_WEEK.md | AUTONOMOUS_V4_WAVE_CLOSURE.md |
|---|---|---|---|---|
| `av4.kickoff.started` | Mode `AV4 Kickoff`; AV4 snapshot `🚧 Kickoff started (plan + backlog published)` | Title `# PLAN — Next Wave (AV4 Kickoff Started)`; snapshot `- AV4 kickoff package is started (...)` | Title `# BACKLOG — Next Wave (AV4 Kickoff Queue)`; baseline `- AV4 kickoff: 🚧 started` | Status `🚧 Open — closure blocked (pending merge/completion evidence)` |
| `av4.execution.in_progress` | Mode `AV4 Execution`; AV4 snapshot `🏗️ Execution in progress (P0 slices actively shipping)` | Title `# PLAN — Next Wave (AV4 Execution In Progress)`; snapshot `- AV4 execution is in progress (...)` | Title `# BACKLOG — Next Wave (AV4 Active Delivery Queue)`; baseline `- AV4 execution: 🏗️ in progress` | Status `🏗️ Open (execution in progress)` |
| `av4.stabilization.started` | Mode `AV4 Stabilization`; AV4 snapshot `🧪 Stabilization started (smoke + release gates in focus)` | Title `# PLAN — Next Wave (AV4 Stabilization Active)`; snapshot `- AV4 stabilization is active (...)` | Title `# BACKLOG — Next Wave (AV4 Stabilization Queue)`; baseline `- AV4 stabilization: 🧪 started` | Status `🧪 Open (stabilization active)` |
| `av4.closed` | Mode `AV4 Closed`; AV4 snapshot `✅ Closed (execution + stabilization complete)` | Title `# PLAN — Next Wave (Post-AV4 Planning)`; snapshot `- AV4 wave (AV4-001 ~ AV4-014) is complete and closed on main.` | Title `# BACKLOG — Next Wave (Post-AV4 Intake Queue)`; baseline `- AV4 closure: ✅ complete (AV4-001 ~ AV4-014)` | Status `✅ Closed on main` |
| `av5.kickoff.started` | Mode `AV5 Kickoff Active`; AV4 snapshot remains `✅ Closed (execution + stabilization complete)` while AV5 kickoff starts | Title `# PLAN — Next Wave (AV5 Kickoff Active)`; snapshot `- AV5 kickoff package is started (...)` | Title `# BACKLOG — Next Wave (AV5 Kickoff Queue)`; baseline `- AV5 kickoff: 🚧 started (...)` | Status `✅ Closed on main` |
| `av6.kickoff.started` | Mode `AV6 Kickoff Active`; AV5 checkpoint is noted while AV4 closure remains intact | Title `# PLAN — Next Wave (AV6 Kickoff Active)`; snapshot `- AV6 kickoff package is started (...)` | Title `# BACKLOG — Next Wave (AV6 Kickoff Queue)`; baseline `- AV6 kickoff: 🚧 started (...)` | Status remains `✅ Closed on main` (AV4 closure ledger frozen) |
| `av6.execution.in_progress` *(draft)* | Mode `AV6 Execution Active`; AV6 wave moves from kickoff packet publication into active delivery | Title `# PLAN — Next Wave (AV6 Execution Active)`; snapshot `- AV6 execution is in progress (...)` | Title `# BACKLOG — Next Wave (AV6 Active Delivery Queue)`; baseline `- AV6 execution: 🏗️ in progress (...)` | Status remains `✅ Closed on main` (AV4 closure ledger frozen) |
| `av6.stabilization.started` *(draft)* | Mode `AV6 Stabilization Active`; AV6 scope is feature-complete and release evidence focus begins | Title `# PLAN — Next Wave (AV6 Stabilization Active)`; snapshot `- AV6 stabilization is active (...)` | Title `# BACKLOG — Next Wave (AV6 Stabilization Queue)`; baseline `- AV6 stabilization: 🧪 started (...)` | Status remains `✅ Closed on main` (AV4 closure ledger frozen) |
| `av6.closed` *(draft)* | Mode `AV6 Closed`; AV6 closure evidence is complete and next-wave intake can begin | Title `# PLAN — Next Wave (Post-AV6 Planning)`; snapshot `- AV6 wave (...) is complete and closed on main.` | Title `# BACKLOG — Next Wave (Post-AV6 Intake Queue)`; baseline `- AV6 closure: ✅ complete (...)` | Status remains `✅ Closed on main` (AV4 closure ledger frozen) |

## Transition runbook: AV5 checkpoint → AV6 kickoff/execution/stabilization/closure

Use this runbook when AV5 checkpoint docs are published and AV6 must move through a deterministic lifecycle from kickoff to closure.

### Canonical transition steps (kickoff)

1. **Confirm clean base on `main`:**
   - `git fetch origin && git checkout main && git pull --ff-only origin main`
2. **Validate registry before mutation:**
   - `python3 scripts/status_board_automation.py --validate-registry`
3. **Verify source state with event detection:**
   - `python3 scripts/status_board_automation.py --detect-event`
4. **Dry-run the target transition:**
   - `python3 scripts/status_board_automation.py av6.kickoff.started --dry-run`
5. **Apply the canonical kickoff event:**
   - `python3 scripts/status_board_automation.py av6.kickoff.started`
6. **Confirm no drift after apply:**
   - `python3 scripts/status_board_automation.py av6.kickoff.started --drift-check`

### Lifecycle promotion flow (AV6 draft sequence)

1. `av6.kickoff.started` → kickoff docs baseline (published plan/backlog + status sync)
2. `av6.execution.in_progress` *(draft)* → delivery mode (ticket implementation and validation in flight)
3. `av6.stabilization.started` *(draft)* → feature-freeze mode (smoke/release checks prioritized)
4. `av6.closed` *(draft)* → closure mode (evidence sealed; next-wave intake enabled)

> Draft note: AV6 post-kickoff events are documented here as transition semantics for planning/review.
> Canonical automation support is currently guaranteed for `av6.kickoff.started`; promote additional AV6 events in registry/automation when corresponding docs lanes are finalized.

### Fallback command flow (manual recovery)

1. `python3 scripts/status_board_automation.py --detect-event`
2. `python3 scripts/status_board_automation.py av6.kickoff.started --force-lock`
3. `python3 scripts/status_board_automation.py --replay <entry_id|index>`
4. `python3 scripts/status_board_automation.py --replay <entry_id|index> --apply`
5. `python3 scripts/status_board_automation.py av6.kickoff.started --drift-check`

## Notes

- Use `python3 scripts/status_board_automation.py --validate-registry` to verify schema/registry integrity.
- Use `python3 scripts/status_board_automation.py <event> --drift-check` to enforce no-doc-drift in CI.
- Apply/drift-check/replay append audit entries to `artifacts/status-hooks/status-hook-audit.jsonl` (override with `--audit-log`).

## Related docs

- `docs/AUTONOMOUS_V6_WAVE_PLAN.md`
- `docs/AUTONOMOUS_V6_BACKLOG.md`
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/STATUS_BOARD_CURRENT.md`
