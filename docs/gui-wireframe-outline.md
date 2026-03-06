# GUI Wireframe Outline (Text)

## 1) Global Layout

```
+--------------------------------------------------------------------------------+
| Top Nav: [Logo] [Project Selector] [Search Runs] [Role View] [User Menu]     |
+----------------------+---------------------------------------------------------+
| Left Sidebar         | Main Content                                            |
| - Dashboard          |                                                         |
| - Runs               |                                                         |
| - Artifacts          |                                                         |
| - Validation         |                                                         |
| - Settings           |                                                         |
+----------------------+---------------------------------------------------------+
| Footer: API status | worker status | last sync timestamp                        |
+--------------------------------------------------------------------------------+
```

---

## 2) Dashboard

```
+--------------------------------------------------------------------------------+
| Dashboard                                                                    [] |
+--------------------------------------------------------------------------------+
| KPI Row                                                                         |
| [Active Runs] [Failed Runs 24h] [Pass Rate] [Avg Duration] [Retry Rate]        |
+--------------------------------------------------------------------------------+
| Active Runs (table)                                                             |
| Run ID | Profile | Phase | Progress | Started | Elapsed | Blocking | Actions   |
| ...                                                                            |
+--------------------------------------------------------------------------------+
| Recent Completed                                                                |
| Run ID | Status | Model | Duration | Soft Fail | Hard Fail | Unresolved        |
+--------------------------------------------------------------------------------+
| Right Panel:                                                                    |
| - Top failing validators                                                        |
| - Latest alerts (checkpoint stale / tool unavailable / fail-fast)               |
+--------------------------------------------------------------------------------+
```

Role emphasis:
- Evaluator: Recent Completed + quality KPIs first.
- Operator: Active Runs + alerts first.
- Developer: quick links to run detail and failed tasks.

---

## 3) Run Detail

```
+--------------------------------------------------------------------------------+
| Run Detail: <run_id>    [Status Chip] [Profile] [Model] [Start/End] [Actions] |
+--------------------------------------------------------------------------------+
| Tabs: [Overview] [Tasks] [Validation] [Artifacts] [Trace] [Checkpoint]         |
+--------------------------------------------------------------------------------+
| Overview Tab                                                                     |
| - Phase Timeline: prd_analysis -> architecture -> planning -> implementation... |
| - Summary cards: total tasks / passed / failed / skipped / fix loops            |
| - Unresolved blockers list                                                       |
+--------------------------------------------------------------------------------+
| Action bar (role-gated): [Resume] [Retry Failed] [Stop] [Export Evidence]      |
+--------------------------------------------------------------------------------+
```

---

## 4) Tasks View (inside Run Detail)

```
+--------------------------------------------------------------------------------+
| Task List                                                                        |
+--------------------------------------------------------------------------------+
| ID | Title | Status | Attempts | Validator Focus | Last Result | Depends On     |
| T1 | ...                                                                          |
+--------------------------------------------------------------------------------+
| Task Detail Drawer (on row click)                                                |
| - Goal / Acceptance / Files                                                      |
| - Attempt trend chart                                                            |
| - Fix loop history                                                               |
| - Links: task_<id>_quality.json, task_<id>_last_validation.json                 |
+--------------------------------------------------------------------------------+
```

---

## 5) Validation Page

```
+--------------------------------------------------------------------------------+
| Validation                                                                       |
+--------------------------------------------------------------------------------+
| Filters: [Task] [Validator] [Status: passed/failed/soft_fail/skipped] [Phase]   |
+--------------------------------------------------------------------------------+
| Matrix View                                                                      |
|              ruff   mypy   pytest   bandit   semgrep   pip_audit   docker_build |
| Task T1       P      F       P        P        S          S            -         |
| Task T2       P      P       P        P        P          S            P         |
+--------------------------------------------------------------------------------+
| Detail Panel                                                                      |
| - command                                                                         |
| - return code / duration / tool version                                          |
| - stdout/stderr (collapsible, searchable)                                        |
| - diagnostics (for pytest: failed tests, locations, assertions)                  |
+--------------------------------------------------------------------------------+
```

Legend: P=passed, F=failed(blocking), S=soft_fail, -=not run.

---

## 6) Artifacts Page

```
+--------------------------------------------------------------------------------+
| Artifacts                                                                        |
+--------------------------------------------------------------------------------+
| Left Tree                                                                         |
| - Input                                                                          |
|   - prd_struct.json                                                              |
|   - prd_analysis.json                                                            |
| - Planning                                                                        |
|   - plan.json                                                                     |
|   - architecture.json                                                             |
| - Execution                                                                       |
|   - checkpoint.json                                                               |
|   - run_trace.json                                                                |
| - Quality                                                                         |
|   - task_quality_index.json                                                       |
|   - task_final_last_validation.json                                               |
| - Report                                                                          |
|   - REPORT.md                                                                     |
+----------------------------------+---------------------------------------------+
| Viewer                           | Metadata                                     |
| JSON/MD content                  | size / updated_at / checksum / download btn  |
+----------------------------------+---------------------------------------------+
```

---

## 7) Settings Page

```
+--------------------------------------------------------------------------------+
| Settings                                                                         |
+--------------------------------------------------------------------------------+
| Section A: Run Defaults                                                          |
| - Default out root                                                               |
| - Default profile                                                                |
| - Max parallel tasks                                                             |
| - Budget max tokens                                                              |
+--------------------------------------------------------------------------------+
| Section B: LLM                                                                   |
| - base_url                                                                        |
| - model                                                                           |
| - timeout_sec                                                                     |
| - role temperatures                                                               |
| - auth source (masked)                                                           |
+--------------------------------------------------------------------------------+
| Section C: Validator Policy                                                      |
| - enabled validators by profile                                                  |
| - per-task soft-fail list                                                        |
| - final soft-fail list                                                           |
+--------------------------------------------------------------------------------+
| Section D: Access & Audit                                                        |
| - role mapping                                                                    |
| - audit event retention                                                           |
+--------------------------------------------------------------------------------+
```

---

## 8) Key Interaction Patterns

1. **Start Run**
   - Modal: PRD path, output root, profile, model override, flags (`--interactive`, resume disabled on new run).
2. **Resume Run**
   - Confirm checkpoint availability and show completed/failed/skipped counts before execute.
3. **Retry Failed**
   - Optional scope: only failed tasks vs full rerun.
4. **Export Evidence**
   - Bundle report + quality summary + final validation + selected logs.

---

## 9) Empty / Error States

- No runs found: show onboarding card with sample command.
- Artifact missing: show non-blocking warning and path info.
- Parse error (partial write): auto-retry parsing with backoff and show "updating" state.
- Permission denied action: show role requirement and request path.
