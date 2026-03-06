# Showoff Delivery Roadmap (Reality-Based)

## Context / Baseline

This roadmap is intentionally grounded in the current repository state:

- GUI MVP already exists as a static web app + read-only API server (`autodev/gui_mvp_server.py`, `autodev/gui_mvp_static/*`).
- Function-level backend scaffolding for safer run start/resume and artifact reads exists (`autodev/gui_api.py`).
- Current GUI limitations are documented in `README.md` (no live streaming, no stop/kill/retry controls, no artifact schema versioning).

Related docs:
- `docs/gui-requirements.md`
- `docs/gui-backend-api.md`
- `docs/gui-wireframe-outline.md`

---

## P0 (Week 1): Demo-Ready Reliability and Operator Basics

### Item P0-1 — Stabilize run discovery/detail APIs
- **Goal:** Make run list/detail behavior predictable across partial and completed runs.
- **Implementation Scope:**
  - Harden parsing against missing or malformed `.autodev` JSON.
  - Unify status derivation logic between `gui_mvp_server` and `gui_api`.
  - Add explicit error payload shape for “run missing / artifact missing / invalid JSON”.
- **Success KPI:**
  - ≥95% success on synthetic fixture matrix (partial, failed, completed, malformed).
  - No uncaught exceptions for known fixture set in CI.
- **Risk:**
  - Hidden assumptions in existing artifact shapes may break normalizers.

### Item P0-2 — Expose minimal run control endpoint (start/resume dry-run + execute)
- **Goal:** Move from read-only GUI to controlled start/resume initiation.
- **Implementation Scope:**
  - Wire `build_start_command`, `build_resume_command`, `trigger_*` into HTTP endpoints.
  - Keep strict allowlist validation and shell-free execution.
  - Add basic API-level input validation errors.
- **Success KPI:**
  - Start/resume requests produce reproducible command payloads.
  - Happy-path subprocess spawn works in local smoke test.
- **Risk:**
  - Runtime environment differences (PATH, venv, permissions) can break execution.

### Item P0-3 — Audit event persistence (file-backed)
- **Goal:** Make mutating actions traceable beyond in-memory response.
- **Implementation Scope:**
  - Persist audit events for start/resume actions under run root (or dedicated audit path).
  - Include actor placeholder, timestamp, action, sanitized command, result.
- **Success KPI:**
  - 100% of mutating requests generate durable audit record.
  - Audit schema documented and covered by tests.
- **Risk:**
  - File-lock/contention issues under concurrent actions.

### Item P0-4 — MVP UI control panel integration
- **Goal:** Allow operator to start/resume from GUI using the new API.
- **Implementation Scope:**
  - Add simple form for PRD path, output root, profile, optional model, interactive toggle.
  - Show immediate response (accepted/error) and recent audit rows.
- **Success KPI:**
  - End-to-end demo: submit run → visible in list within polling cycle.
- **Risk:**
  - UX complexity creep (must stay minimal in week 1).

### Item P0-5 — Demo harness and fixtures
- **Goal:** Ensure repeatable showoff demo, independent of ad-hoc local state.
- **Implementation Scope:**
  - Add deterministic sample run fixture set (ok/failed/running).
  - Add script to boot GUI server and verify health/API contracts.
- **Success KPI:**
  - One command reproduces baseline demo environment on clean machine.
- **Risk:**
  - Fixture drift if internal artifact fields change.

---

## P1 (Weeks 2–4): Better Observability and Triage Flow

### Item P1-1 — Event timeline and phase diagnostics
- **Goal:** Improve debuggability for “where did the run stall/fail?”.
- **Implementation Scope:**
  - Expand phase timeline UI with duration + state details.
  - Surface key run trace events in chronological order.
- **Success KPI:**
  - Operator can identify failing phase in <30 seconds during usability check.
- **Risk:**
  - Large traces can degrade frontend responsiveness.

### Item P1-2 — Validation triage shortcuts
- **Goal:** Reduce time from failed validator to actionable context.
- **Implementation Scope:**
  - Add direct links from validation rows to related task/artifact data.
  - Add basic filter presets (failed only, blocker only, validator name).
- **Success KPI:**
  - Median clicks-to-context reduced vs current flow (manual spot check).
- **Risk:**
  - Inconsistent artifact linkage across templates/runs.

### Item P1-3 — Run comparison (A/B by profile/model)
- **Goal:** Support evaluator workflows for comparing outcomes.
- **Implementation Scope:**
  - Select two runs and compare summary metrics, validation outcomes, blockers.
- **Success KPI:**
  - Side-by-side view works for at least 20 recent runs without crash.
- **Risk:**
  - Metric definitions may be ambiguous without normalization rules.

### Item P1-4 — Schema compatibility guardrails
- **Goal:** Reduce breakage from artifact schema drift.
- **Implementation Scope:**
  - Introduce lightweight schema version markers and compatibility adapters.
  - Add warning banner when unsupported schema is detected.
- **Success KPI:**
  - Known old fixture versions still render with fallback paths.
- **Risk:**
  - Extra compatibility layer can increase maintenance cost.

### Item P1-5 — Access control baseline
- **Goal:** Prevent accidental misuse of mutating actions.
- **Implementation Scope:**
  - Minimal role model (`viewer`, `operator`, `admin`) for server actions.
  - Enforce start/resume permissions at API layer.
- **Success KPI:**
  - Unauthorized mutation attempts are blocked and audited.
- **Risk:**
  - Auth setup complexity may delay adoption in local-only setups.

---

## P2 (After Week 4): Expansion and Hardening

### Item P2-1 — Controlled stop/retry workflows
- **Goal:** Add safe operator controls beyond start/resume.
- **Implementation Scope:**
  - Process tracking, stop/terminate behavior, and retry policy with guardrails.
- **Success KPI:**
  - Stop/retry actions are deterministic and leave consistent state.
- **Risk:**
  - Process ownership/orphan handling is OS-dependent.

### Item P2-2 — Cross-run quality analytics
- **Goal:** Identify recurring failure fingerprints and quality trends.
- **Implementation Scope:**
  - Aggregate validator outcomes and blocker patterns across runs.
  - Provide trend widgets (time-windowed pass/fail, top failing validators).
- **Success KPI:**
  - Weekly trend summary generated from local run history.
- **Risk:**
  - Incomplete historical data can produce misleading trends.

### Item P2-3 — Policy editor + validator graph tuning UI
- **Goal:** Make quality profile tuning easier without direct YAML editing.
- **Implementation Scope:**
  - Read/write views for quality profile and validator graph settings.
  - Dry-run validation of policy changes before apply.
- **Success KPI:**
  - Operator can safely modify policy with rollback path.
- **Risk:**
  - Misconfiguration risk increases if guardrails are weak.

### Item P2-4 — External integration hooks (notifications/ticketing)
- **Goal:** Improve team workflow handoff from run results.
- **Implementation Scope:**
  - Webhook events for run completion/failure.
  - Optional integration adapters for chat/ticket systems.
- **Success KPI:**
  - Completion/failure event delivered with run context.
- **Risk:**
  - Secret management and outbound reliability requirements grow.

---

## Delivery Notes

- This roadmap prioritizes “demo reliability + operational clarity” over broad feature count.
- P0 should be executable without rewriting core orchestration.
- P1/P2 depend on artifact contract stabilization and clean API boundaries first.
