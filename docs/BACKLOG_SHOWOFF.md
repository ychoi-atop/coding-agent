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

### SHW-016 — Extended RBAC & auth integration ✅ done
- **Priority:** P1
- **Effort:** M
- **Owner role:** backend
- **Acceptance Criteria:**
  - Integrate role source with real authn/authz provider (token/session-backed).
  - Add per-project or per-environment policy scoping.
  - Preserve audit compatibility with SHW-005/006.
- **Completion notes (2026-03-06):**
  - Mutating endpoint auth role resolution now supports token/session-backed sources via `AUTODEV_GUI_AUTH_CONFIG` (JSON file):
    - bearer token (`Authorization: Bearer ...`) or `X-Autodev-Token`
    - session (`X-Autodev-Session` or `autodev_session` cookie)
    - fallback remains header/env (`X-Autodev-Role`, `AUTODEV_GUI_ROLE`) for backward compatibility
  - Added scoped policy matching by `project` and/or `environment`, with per-action allowed roles (`actions.<action>`) and default policy fallback.
  - Audit log keeps SHW-005/006 compatibility (`role`, `payload`, `result_status`) and adds optional `auth` metadata (`source/subject/scope/policy_*`).
  - Added tests for token/session resolution + scope-policy allow/deny and endpoint audit assertions.
- **Dependencies:** SHW-006

### SHW-017 — Controlled stop/retry process manager ✅ done
- **Priority:** P2
- **Effort:** L
- **Owner role:** platform
- **Acceptance Criteria:**
  - Server tracks spawned run processes and state transitions.
  - Stop action handles graceful termination + forced kill fallback.
  - Retry action preserves audit chain and run linkage.
- **Completion notes (2026-03-06):**
  - `autodev/gui_process_manager.py` 추가: GUI가 spawn한 프로세스를 `process_id` 기준으로 추적하고, 전이 이력(`transitions`)을 기록.
  - process state를 file-backed store(`artifacts/gui-process/process-state.json`, env override 지원)로 영속화하여 서버 restart 후에도 조회/감사 가능.
  - `POST /api/runs/stop` 추가: `terminate()` graceful 대기 후 timeout 시 `kill()` fallback.
  - `POST /api/runs/retry` 확장: `process_id` 뿐 아니라 `run_id` 기반 retry 지원(해당 run의 latest tracked process 선택), `retry_of/retry_root/retry_attempt` 체인 유지.
  - read endpoint 추가: `GET /api/processes`, `GET /api/processes/{process_id}`, `GET /api/processes/{process_id}/history`.
  - mutating endpoint audit payload에 `process_id`, `run_id`, `graceful_timeout_sec`가 포함되어 stop/retry 감사 추적 가능.
  - 테스트 추가: persistence reload, process list/detail/history, retry-by-run-id, start/stop/retry happy path + unknown target/invalid payload/forced kill failure path.
- **Dependencies:** SHW-003, SHW-004, SHW-005

### SHW-018 — Cross-run quality trend aggregation
- **Priority:** P2
- **Effort:** L
- **Owner role:** backend
- **Status:** ✅ Done (2026-03-06)
- **Acceptance Criteria:**
  - Aggregation job produces trend metrics for validators/blockers over time.
  - API endpoint serves bounded historical window.
  - Missing historical artifacts are skipped with explicit counters.
- **Completion notes (2026-03-06):**
  - `autodev/gui_mvp_server.py`에 `_quality_trends()` 집계 추가: run-window 기반 validator/blocker cross-run metric 산출.
  - `GET /api/runs/trends?window=<N>` endpoint 추가 (default=20, bounded 1..200).
  - artifact 누락/손상 skip 카운터(`runs_skipped_missing_*`, `runs_skipped_invalid_*`)를 payload `counters`로 노출.
  - SHW-018 follow-up: optional partial aggregation mode 추가 (`partial=true` / `allow_partial=true`).
    - strict default(기존 동작) 유지: artifact 하나라도 없으면 skip.
    - partial mode에서 단일 missing artifact run을 부분 포함하고 `runs_included_partial*` 카운터로 명시.
  - 정상 + sparse/missing + partial mode 시나리오 테스트 추가 (`autodev/tests/test_gui_mvp_server.py`).
- **Dependencies:** SHW-015

---

## Suggested Immediate Start Queue (No Blocking Dependencies)

- SHW-016~018 범위는 완료. 다음 우선순위는 별도 planning에서 재정의.
