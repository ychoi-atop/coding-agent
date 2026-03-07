# PR Draft — Showoff Phase 6 (Track B: Process panel stability)

## Title
`fix(gui-showoff): harden process panel selection/loading/action guards for edge-case stability`

## Summary
This PR implements **Phase 6 / Track B** for Process panel robustness in the local-simple GUI.

### Included
1. **Empty list refresh race hardening**
   - Adds request-sequence guard (`processLoadRequestSeq`) so stale `/api/processes` responses cannot override newer state.
   - Adds explicit refresh status messaging while list fetch is in-flight.
2. **Selection stability when rows disappear**
   - Adds guard handling when selected process is removed (backend lifecycle, filter mismatch, or 404 detail fetch).
   - Falls back cleanly to available selection or empties detail pane with clear operator-facing message.
3. **Stop/Retry in-flight safety**
   - Adds client-side in-flight lock (`processActionInFlight`, `processActionType`) to prevent duplicate stop/retry submissions.
   - Disables Stop/Retry buttons during pending request and updates button labels (`Stopping…`, `Retrying…`).
4. **Status clarity + lightweight guards**
   - Adds dedicated process status helper for non-ambiguous panel messages.
   - Improves no-data/no-filter-match messaging so operators know whether to refresh, clear filters, or trigger a new run.
5. **Test coverage updates**
   - Extends Process panel static contract checks to include new guard functions/state, race-guard line, and operator status copy.

## Why
- Process list refresh can be triggered by polling, tab activity, and manual refresh; stale responses should not regress UI state.
- Operators can lose selected process context when filtering/pagination changes or when process exits and drops from list.
- Stop/Retry controls need safe, deterministic UX during in-flight requests to avoid duplicate mutation calls.

## Scope / Non-Goals
- No backend API contract change.
- No new API endpoints.
- No RBAC/audit behavior changes.

## Implementation Notes
- `autodev/gui_mvp_static/app.js`
  - Added process status helper (`setProcessStatus`).
  - Added client-side process-panel guard state:
    - `processLoadRequestSeq`
    - `processLoadInFlight`
    - `processActionInFlight`
    - `processActionType`
  - Added `syncProcessActionButtons(process)` to centralize button disable/label behavior.
  - Hardened `refreshProcessPanel()` selection sync and empty/filter-hidden status behavior.
  - Hardened `selectProcess()` missing-selection recovery path (404 fallback + re-sync).
  - Hardened `loadProcesses()` with stale-response guard.
  - Hardened `runProcessAction()` with in-flight lock and explicit status updates.
- `autodev/tests/test_gui_mvp_server.py`
  - Extended process static contract checks for new guard logic and status-message contract strings.

## Suggested PR Body (copy/paste)
```md
## What
- harden process list refresh path with stale-response sequence guard
- improve process selection recovery when selected row disappears
- add in-flight lock and deterministic Stop/Retry button state/labels
- add clear process-panel status messaging for empty/filter-hidden/error cases
- extend static contract tests for process panel stability guards

## Why
- prevent refresh races from overwriting newer process state
- avoid broken/stale selection when process rows disappear
- prevent duplicate stop/retry actions during pending requests
- improve operator clarity during triage and demo workflows

## Validation
- pytest autodev/tests/test_gui_mvp_server.py -q -k "process_panel_static_contract or process_read_endpoints_list_detail_history or stop_and_retry_endpoints_happy_path or retry_endpoint_supports_run_id_target"
- pytest autodev/tests/test_gui_api.py -q -k "trigger_retry_by_run_id_preserves_chain or trigger_start_execute_tracks_process_and_stop_graceful or trigger_stop_forced_kill_fallback"
```

## Manual Verification Checklist
- [ ] Rapidly click **Refresh** while polling is active; process list remains coherent (no stale overwrite flicker).
- [ ] Apply filter that hides current selection; status line clearly indicates selection is hidden by filters.
- [ ] Clear filters restores visible row selection and detail pane.
- [ ] Delete/finish selected process (or force 404 detail path); panel recovers without broken controls.
- [ ] Click **Stop process** once; button disables and label switches to `Stopping…` until completion.
- [ ] Click **Retry process** once; button disables and label switches to `Retrying…` until completion.
- [ ] While one action is in-flight, additional stop/retry clicks are blocked with status guidance.

## Risks / Rollback
- Risk is low and frontend-local to Process panel state management.
- Rollback by reverting this PR; backend process manager and API contracts are unaffected.
