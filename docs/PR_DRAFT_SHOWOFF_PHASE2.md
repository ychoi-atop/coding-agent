# PR Draft — Showoff Phase 2 (SHW-009..015)

## Suggested PR title
feat(gui-showoff): ship validation UX + run comparison + schema markers (SHW-009/011/012/013/014, partial SHW-010/015)

## Suggested PR body

## Summary
This PR advances Showoff Phase 2 by improving validation triage UX, adding run-to-run comparison support, and introducing artifact schema markers with compatibility fallback warnings.

Implemented scope in this PR:
- **SHW-009** Validation UX polish (status chips/grouping, failed-first toggle, search/filter/sort, expandable outputs)
- **SHW-011** Validation triage deep-linking context (task/artifact aware rows + clickable triage flow)
- **SHW-012** Run comparison API + DTO normalization (explicit defaults, legacy alias handling, summary delta payload)
- **SHW-013** Compare UI (A/B selector, highlighted diffs, API-first with adapter fallback)
- **SHW-014** Artifact schema version markers + unknown-version warning with fallback path

Not fully completed in this PR:
- **SHW-010** Timeline enrichment is partially improved via DTO/timeline rendering but ticket is still open in backlog.
- **SHW-015** Full compatibility adapter layer remains intentionally partial (marker + fallback path only).

## Key changes

### Backend/API
- Added schema marker utility:
  - `autodev/gui_artifact_schema.py` (new)
- Wired schema markers into read/list/detail APIs:
  - `autodev/gui_api.py`
  - `autodev/gui_mvp_server.py`
- Added compare endpoint:
  - `GET /api/runs/compare?left=<run_id>&right=<run_id>`
  - Supports legacy aliases `run_a` / `run_b`
  - Returns normalized left/right summaries + numeric deltas

### DTO/Normalization
- Extended run/validation normalization and comparison DTO generation:
  - `autodev/gui_mvp_dto.py`
- Added explicit defaulting for mixed/legacy schemas and validator outcome normalization.

### Frontend (GUI MVP static)
- Validation tab UX upgrades:
  - richer filters (status + validator + search)
  - severity/name/duration sorting + failed-first toggle
  - grouped cards and summary chips
  - stderr/stdout expandable output blocks
  - triage panel for failed validator context
- New Compare tab:
  - left/right run selection, swap/refresh actions
  - compare source badge (API vs fallback)
  - side-by-side metrics + highlighted diff list
- Files:
  - `autodev/gui_mvp_static/index.html`
  - `autodev/gui_mvp_static/styles.css`
  - `autodev/gui_mvp_static/app.js`

### Tests & Docs
- Expanded coverage for schema marker behavior, comparison endpoint, DTO compatibility/defaults, and triage fields:
  - `autodev/tests/test_gui_api.py`
  - `autodev/tests/test_gui_mvp_server.py`
  - `autodev/tests/test_gui_mvp_dto.py`
- Updated docs:
  - `docs/gui-backend-api.md`
  - `docs/BACKLOG_SHOWOFF.md`

## Changed files
- `autodev/gui_api.py`
- `autodev/gui_artifact_schema.py` (new)
- `autodev/gui_mvp_dto.py`
- `autodev/gui_mvp_server.py`
- `autodev/gui_mvp_static/app.js`
- `autodev/gui_mvp_static/index.html`
- `autodev/gui_mvp_static/styles.css`
- `autodev/tests/test_gui_api.py`
- `autodev/tests/test_gui_mvp_dto.py`
- `autodev/tests/test_gui_mvp_server.py`
- `docs/BACKLOG_SHOWOFF.md`
- `docs/gui-backend-api.md`

## Test commands run
```bash
.venv/bin/python -m pytest -q autodev/tests/test_gui_api.py autodev/tests/test_gui_mvp_dto.py autodev/tests/test_gui_mvp_server.py
.venv/bin/python -m pytest -q autodev/tests/test_gui_*.py
```

## Test outcomes
- `39 passed in 3.63s`
- `39 passed in 3.61s`

## Risks / follow-ups
- Full SHW-015 compatibility adapter coverage (fixture snapshot breadth + additional schema variants) should be completed in a follow-up PR.
- SHW-010 timeline enrichment can be finalized with richer labels/tooltips and stress checks for long traces.
