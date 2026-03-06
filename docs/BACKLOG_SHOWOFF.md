# Showoff Implementation Backlog

This backlog decomposes the roadmap into implementation-ready tickets.

References:
- `docs/ROADMAP_SHOWOFF.md`
- `docs/gui-requirements.md`
- `docs/gui-backend-api.md`

## Ticket Format

- **Priority:** P0 / P1 / P2
- **Effort:** S / M / L
- **Owner role:** backend / frontend / platform

---

## Tickets

### SHW-001 — Run status normalization contract ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - Single status mapper used by both `gui_mvp_server` and `gui_api`.
  - Contract documented for `ok`, `failed`, `running`, `unknown`.
  - Tests cover metadata/checkpoint conflict cases.
- **Dependencies:** None

### SHW-002 — Robust artifact JSON loading policy ✅ done
- **Priority:** P0
- **Effort:** S
- **Owner role:** backend
- **Acceptance Criteria:**
  - Malformed JSON returns typed error payload (not server exception).
  - Parse errors include file path and reason code.
  - Existing tests updated for new behavior.
- **Dependencies:** SHW-001

### SHW-003 — API endpoint for start run (dry-run + execute) ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - HTTP endpoint accepts `prd/out/profile/model/interactive/config`.
  - Dry-run returns command preview and audit event.
  - Execute mode spawns process with shell disabled.
- **Dependencies:** SHW-001

### SHW-004 — API endpoint for resume run ✅ done
- **Priority:** P0
- **Effort:** S
- **Owner role:** backend
- **Acceptance Criteria:**
  - Resume endpoint appends `--resume` consistently.
  - Response includes spawn status and audit event.
  - Invalid input paths/tokens return 4xx.
- **Dependencies:** SHW-003

### SHW-005 — File-backed audit log writer ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** platform
- **Acceptance Criteria:**
  - Every start/resume request persisted to audit log file.
  - Log entry includes action, timestamp, payload summary, result status, error(optional).
  - Write failure is surfaced as explicit API error (`audit_persist_failed`) without process crash.
- **Dependencies:** SHW-003, SHW-004

### SHW-006 — Minimal RBAC for mutating endpoints ✅ done
- **Priority:** P0
- **Effort:** S
- **Owner role:** backend
- **Acceptance Criteria:**
  - Start/resume endpoints enforce role checks.
  - Role source: `X-Autodev-Role` header, fallback `AUTODEV_GUI_ROLE`, default `evaluator`.
  - Unauthorized attempts return 403 and are audited.
- **Dependencies:** SHW-005

### SHW-007 — Strong resume target validation semantics ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - Resume target must contain `.autodev/checkpoint.json` and `.autodev/run_metadata.json`.
  - Checkpoint resumable markers and run metadata consistency are validated.
  - Terminal runs and inconsistent markers return explicit 4xx actionable errors.
- **Dependencies:** SHW-004

### SHW-008 — Demo fixture dataset generator ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** platform
- **Acceptance Criteria:**
  - Script generates deterministic sample runs (`ok`, `failed`, `running`).
  - Fixture schema matches current `.autodev` expectations.
  - Script documented in demo playbook.
- **Dependencies:** None

### SHW-009 — Validation UX polish (GUI MVP) ✅ done
- **Priority:** P0
- **Effort:** M
- **Owner role:** frontend
- **Acceptance Criteria:**
  - Validation tab surfaces clear status badges, grouped sections, and summary chips.
  - Validator cards support severity-based sorting with failed-first toggle.
  - stderr/stdout details are expandable and searchable.
  - Empty and filter-zero states remain explicit and non-breaking.
  - DTO tests cover transformed validation rows (status aliases, output fields, severity rank).
- **Dependencies:** SHW-008

### SHW-010 — Phase timeline enrichment
- **Priority:** P1
- **Effort:** M
- **Owner role:** frontend
- **Acceptance Criteria:**
  - Timeline shows phase durations with clear labels/tooltips.
  - Empty/partial timelines handled gracefully.
  - Rendering remains responsive for larger traces.
- **Dependencies:** SHW-001

### SHW-011 — Validation triage deep-linking ✅ done
- **Priority:** P1
- **Effort:** M
- **Owner role:** frontend
- **Acceptance Criteria:**
  - Clicking failed validator opens related task/artifact context.
  - Filters support failed-only and validator name.
  - Works for final and per-task validation data.
- **Dependencies:** SHW-010

### SHW-012 — Run comparison API and DTO ✅ done
- **Priority:** P1
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - API returns comparable summary fields for two run IDs.
  - Missing fields normalized to explicit defaults.
  - Tests cover mixed schema/version cases.
- **Dependencies:** SHW-001, SHW-002

### SHW-013 — Run comparison UI ✅ done
- **Priority:** P1
- **Effort:** M
- **Owner role:** frontend
- **Acceptance Criteria:**
  - User can select two runs and see side-by-side summary.
  - Differences in status, blockers, and key validator outcomes are highlighted.
  - Handles missing data without blank-page errors.
  - UI consumes SHW-012 compare API (`/api/runs/compare`) when available.
  - Adapter fallback computes compare payload from per-run detail endpoints if compare API is unavailable.
- **Dependencies:** SHW-012

### SHW-014 — Artifact schema version marker ✅ done
- **Priority:** P1
- **Effort:** S
- **Owner role:** backend
- **Acceptance Criteria:**
  - Version marker included in API responses for key artifacts.
  - Unknown version triggers warning object in payload.
  - Backward-compatible fallback path documented.
- **Dependencies:** SHW-002

### SHW-015 — Compatibility adapter layer ✅ done
- **Priority:** P1
- **Effort:** L
- **Owner role:** backend
- **Acceptance Criteria:**
  - Adapter supports at least two known artifact variations.
  - Coverage includes run trace + validation normalization.
  - Compatibility tests added with fixture snapshots.
- **Completion notes (2026-03-06):**
  - `normalize_run_trace`가 `events`(legacy) + `phases/phase_timeline`(modern) 변형을 공통 DTO로 정규화.
  - `normalize_validation`이 `validation/results/final.validations/quality fallback` 변형을 공통 `summary + validator_cards`로 정규화.
  - fixture snapshot 테스트 추가: `autodev/tests/fixtures/gui_compat/*.json`.
- **Dependencies:** SHW-014

### SHW-016 — Extended RBAC & auth integration (future)
- **Priority:** P1
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - Integrate role source with real authn/authz provider (token/session-backed).
  - Add per-project or per-environment policy scoping.
  - Preserve audit compatibility with SHW-005/006.
- **Dependencies:** SHW-006

### SHW-017 — Controlled stop/retry process manager
- **Priority:** P2
- **Effort:** L
- **Owner role:** platform
- **Acceptance Criteria:**
  - Server tracks spawned run processes and state transitions.
  - Stop action handles graceful termination + forced kill fallback.
  - Retry action preserves audit chain and run linkage.
- **Dependencies:** SHW-003, SHW-004, SHW-005

### SHW-018 — Cross-run quality trend aggregation
- **Priority:** P2
- **Effort:** L
- **Owner role:** backend
- **Acceptance Criteria:**
  - Aggregation job produces trend metrics for validators/blockers over time.
  - API endpoint serves bounded historical window.
  - Missing historical artifacts are skipped with explicit counters.
- **Dependencies:** SHW-015

---

## Suggested Immediate Start Queue (No Blocking Dependencies)

1. SHW-017 — Controlled stop/retry process manager
2. SHW-018 — Cross-run quality trend aggregation
3. SHW-016 — Extended RBAC & auth integration (future)
