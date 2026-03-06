# feat(gui-showoff): PR #3 — local-simple fast path + health banner + compare defaults

## Title
`feat(gui-showoff): phase3 local-simple run fast path, local health banner, compare auto-defaults`

## Summary
This PR delivers a small, mergeable polish scope for Showoff Phase 3:

1. **CLI fast path:** `autodev local-simple --run <PRD_PATH>` now starts GUI and immediately kicks off a local quick run.
2. **GUI local health banner:** adds a lightweight banner in Run Controls showing gateway/model/context health from existing endpoints (`/healthz`, `/api/gui/context`, run list/detail model info).
3. **Compare UX default behavior:** compare tab now auto-selects the latest two runs when available and avoids preselecting the same run for both sides.

## Why
- Reduce friction for local demo/startup flow.
- Improve operator confidence with immediate health visibility.
- Make Compare useful out-of-the-box without manual selector cleanup.

## Implementation Notes
### 1) `local-simple --run`
- Added `--run` option in `autodev/main.py` under `_cli_local_simple`.
- On startup, if provided:
  - resolves PRD path,
  - sets `AUTODEV_GUI_DEFAULT_PRD`,
  - calls `gui_api.trigger_start(..., execute=True)` with local-simple defaults,
  - logs kickoff process id (best effort), and
  - degrades gracefully if kickoff fails (GUI still serves).

### 2) Health banner
- Added `<div id="localHealthBanner">` in `autodev/gui_mvp_static/index.html`.
- Added banner rendering + refresh logic in `autodev/gui_mvp_static/app.js`:
  - gateway health from `/healthz`,
  - context health/mode from `/api/gui/context`,
  - model health from selected/latest run metadata.
- Added visual styles in `autodev/gui_mvp_static/styles.css`.

### 3) Compare defaults
- Improved compare initialization in `app.js`:
  - auto-picks latest two runs by default,
  - supports single-run state with explicit “Select second run” placeholder,
  - keeps manual compare selection sticky after user override.

## Tests
Updated `autodev/tests/test_main_gui_cli.py`:
- `test_cli_local_simple_run_flag_triggers_quick_run_kickoff`
- `test_cli_local_simple_run_kickoff_failure_is_non_fatal`

## Manual Verification Checklist
- [ ] `autodev local-simple --run examples/PRD.md` starts GUI and auto-spawns run process.
- [ ] Health banner reflects gateway/model/context state after page load.
- [ ] Compare tab auto-selects latest two runs when 2+ runs exist.
- [ ] Compare tab shows clear guidance when only one run exists.
- [ ] Existing local-simple flows (`--open`, localhost guardrails) remain intact.

## Risks / Rollback
- Risk is low and scoped to local-simple UX + frontend state defaults.
- Rollback by reverting this PR branch; no migrations/state changes required.

## Suggested PR Body (copy/paste)
```md
## What
- add `autodev local-simple --run <PRD_PATH>` startup fast path (serve GUI + auto kick off run)
- add local health banner (gateway/model/context)
- improve compare tab defaults to auto-select latest two runs

## Why
- faster local showoff startup
- clearer local runtime confidence
- compare usable by default

## Validation
- pytest autodev/tests/test_main_gui_cli.py -q
- (focused manual) local-simple run kickoff + health banner + compare defaults
```
