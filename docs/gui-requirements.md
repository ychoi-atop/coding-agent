# Coding-Agent GUI Requirements (v0.1 Draft)

## 1. Purpose and Scope

This document defines product and technical requirements for a GUI layer on top of the existing `autodev` CLI workflow.

The GUI must reflect the current run lifecycle and artifact model (not replace it initially), while enabling three key user roles:

- **Evaluator**: assesses run quality and readiness.
- **Operator**: manages execution, retries, and operational stability.
- **Developer**: inspects implementation output and validation details.

Primary scope in this phase:

1. Visualize and control run lifecycle from PRD input to final validation.
2. Surface existing `.autodev/*` artifacts as first-class UI objects.
3. Add role-focused workflows across Dashboard, Run Detail, Artifacts, Validation, and Settings.

Out of scope (initially):

- Rewriting orchestration engine in GUI backend.
- Replacing CLI prompt/role logic.
- Multi-tenant enterprise IAM federation (SSO/SCIM) in MVP.

---

## 2. Current System Baseline (as-is)

### 2.1 Execution Entry and Parameters

Current command entry:

```bash
autodev --prd <file> --out <dir> --profile <name> [--resume] [--interactive] [--model <override>]
```

Important runtime knobs currently in `config.yaml`:

- `run.max_json_repair`
- `run.max_fix_loops_total`
- `run.max_fix_loops_per_task`
- `run.budget.max_tokens`
- `run.max_parallel_tasks`
- `profiles.<name>.validators`
- `profiles.<name>.quality_profile` / soft-fail policy
- `llm.*` (endpoint/auth/model/role temperatures)

### 2.2 Pipeline Phases (already implemented)

The orchestration loop already emits meaningful phase/task boundaries:

1. `prd_analysis`
2. `architecture`
3. `planning`
4. `implementation` (task-level + fix loops)
5. `final_validation`

### 2.3 Existing Artifacts and Telemetry

Per run directory already includes structured payloads suitable for UI consumption:

- `.autodev/run_metadata.json`
- `.autodev/prd_struct.json`
- `.autodev/plan.json`
- `.autodev/checkpoint.json`
- `.autodev/task_quality_index.json`
- `.autodev/task_<id>_quality.json`
- `.autodev/task_<id>_last_validation.json`
- `.autodev/task_final_last_validation.json`
- `.autodev/quality_profile.json`
- `.autodev/quality_run_summary.json`
- `.autodev/quality_resolution.json`
- `.autodev/run_trace.json`
- `.autodev/REPORT.md`

This is a strong base for a GUI without changing core orchestration semantics.

---

## 3. User Roles and Primary Use Cases

## 3.1 Evaluator

**Goal:** Decide whether a run is acceptable for handoff/release.

Key use cases:

- Review final status (`ok`, blockers, hard/soft failures).
- Inspect quality gate results and unresolved blockers.
- Compare run outcomes across profiles or model choices.
- Produce concise evidence package for sign-off.

Needs:

- Fast high-level run scorecard.
- Clear distinction between soft-fail and blocking failures.
- Traceability from final verdict to validator evidence.

## 3.2 Operator

**Goal:** Keep runs flowing reliably and recover from failures quickly.

Key use cases:

- Start run with profile/model/options.
- Resume from checkpoint.
- Monitor active run progress and stuck states.
- Trigger safe rerun/retry workflows.
- Identify environment/tooling failures (`tool_unavailable`, dependency issues).

Needs:

- Live status panel with phase/task progression.
- Control actions (start/resume/retry/stop with safeguards).
- Operational diagnostics and run metadata visibility.

## 3.3 Developer

**Goal:** Understand what changed and why validations failed/passed.

Key use cases:

- Inspect plan/task hierarchy and changed files.
- Drill into validation diagnostics (ruff/mypy/pytest/etc).
- View generated artifacts (PRD normalization, architecture, tests, reviews).
- Correlate failure signatures with fix attempts.

Needs:

- Task-centric detail pages.
- Diff-oriented artifact browser.
- Validation history with iteration timeline.

---

## 4. Information Architecture (IA)

Required top-level areas:

1. **Dashboard**
2. **Run Detail**
3. **Artifacts**
4. **Validation**
5. **Settings**

### 4.1 Dashboard

Primary views:

- Active runs list (phase, elapsed, progress, failures-in-flight).
- Recent completed runs (status, profile, model, duration, blockers).
- Quality snapshot widgets (pass rate, top failing validators, retry hotspots).

### 4.2 Run Detail

Primary views:

- Run summary header (run_id, profile, model, started/finished, status).
- Phase timeline (phase durations and transitions).
- Task graph/list with status (`passed/failed/skipped/resumed`).
- Fix-loop activity and attempt trend.
- Checkpoint state (`completed_task_ids`, failed/skipped IDs).

### 4.3 Artifacts

Primary views:

- Artifact navigator by category:
  - Input/Normalization (`prd_struct`, `prd_analysis`)
  - Plan/Architecture (`plan`, `architecture`, `api_spec`, `db_schema`)
  - Quality (`task_quality_index`, task quality files)
  - Final (`REPORT.md`, `quality_run_summary`, `quality_resolution`)
- JSON/Markdown viewers with search and copy.
- Optional diff mode between two runs.

### 4.4 Validation

Primary views:

- Validator matrix by task × validator.
- Final validation board with blocking vs soft failures.
- Diagnostic panel (stdout/stderr, classifications, tool versions).
- Trend chart for retries, repeated failure signatures, fail-fast triggers.

### 4.5 Settings

Primary views:

- Runtime defaults (output root, profile, parallelism, token budget).
- LLM endpoint/model settings and role temperature controls.
- Validator policy and soft-fail mapping.
- Access control and audit settings.

---

## 5. Functional Requirements (Prioritized)

## 5.1 P0 (MVP-critical)

1. **Run lifecycle visibility**
   - Show run list and run detail using current `.autodev` files.
   - Render phase and task statuses accurately.

2. **Launch and resume controls**
   - Start run with PRD, output root, profile, model override.
   - Resume run from checkpoint.

3. **Validation transparency**
   - Display final and per-task validation outputs.
   - Separate hard vs soft failures.

4. **Artifact access**
   - Browse/download core artifacts (`run_metadata`, `plan`, `checkpoint`, `quality`, `report`).

5. **Role-aware views (lightweight RBAC)**
   - Evaluator default: scorecards + blockers.
   - Operator default: active runs + controls.
   - Developer default: tasks + diagnostics.

6. **Audit events for user actions**
   - Record run start/resume/retry/cancel and configuration changes.

## 5.2 P1 (Post-MVP, near-term)

1. Run comparison (A/B profile/model/quality outcomes).
2. Task dependency graph visualization and skipped-dependency reasoning.
3. Streaming logs/event timeline from run trace + JSON logs.
4. Failure triage shortcuts (open relevant artifact/validator diagnostics directly).
5. Saved run filters and shared views.

## 5.3 P2 (Expansion)

1. Policy-as-code editor for quality profiles and validator graph strategy.
2. Advanced risk analytics (recurring fingerprints, failure taxonomy trends).
3. Multi-project federation dashboard.
4. Approval workflow integrations (chatops/webhooks/ticket updates).

---

## 6. Non-Functional Requirements

## 6.1 Reliability

- GUI must tolerate partial/in-progress artifacts.
- Backend must gracefully handle missing files and stale runs.
- Read-only mode must still function during CLI run execution.
- Idempotent command submission for run start/resume.

## 6.2 Security

- No plaintext secret display (`api_key`, `oauth_token` redaction at API/UI).
- Role-based action controls (at minimum: viewer/operator/admin).
- Validate and sanitize all CLI command inputs.
- Prevent path traversal on artifact browsing endpoints.

## 6.3 Performance

- Dashboard first paint target: <2s for last 50 runs.
- Run detail load target: <1.5s for typical run metadata.
- Incremental updates for long validator stdout/stderr payloads.

## 6.4 Auditability and Traceability

- Every mutating action must be audit logged with actor, timestamp, parameters, result.
- Link UI actions to `run_id`/`request_id` where possible.
- Preserve immutable snapshots for finalized runs.

## 6.5 Access Control

- Baseline RBAC matrix:
  - Evaluator: read run/quality data, no run mutation.
  - Operator: run control actions + read all.
  - Developer: read all artifacts + optional rerun in dev environments.

---

## 7. Data and Backend Integration Requirements

## 7.1 Core Domain Entities

- `Run`
- `Phase`
- `Task`
- `ValidationResult`
- `Artifact`
- `Checkpoint`
- `QualityGateResult`
- `AuditEvent`

## 7.2 Required Data Feeds (from current system)

1. **Run status**
   - Source: `run_metadata.json`, checkpoint, run trace, completion marker.

2. **Logs/events**
   - Source: structured JSON log events + `run_trace.json` + CLI progress callback events.

3. **Checkpoints**
   - Source: `.autodev/checkpoint.json` with completed/failed/skipped task IDs.

4. **Quality gates**
   - Source: `task_quality_index.json`, task-level quality files, final validation file.

5. **Artifacts**
   - Source: `.autodev/*` and generated run files.

## 7.3 API/Backend Contract Expectations

Minimum backend capabilities:

- Enumerate runs by output roots.
- Parse and normalize `.autodev` artifacts into stable API responses.
- Launch CLI subprocess with controlled arguments.
- Stream run status updates (SSE/WebSocket or polling fallback).
- Persist user/audit metadata separate from run artifacts.

---

## 8. MVP Scope (Demo-ready)

### 8.1 MVP Deliverables

- Dashboard with active/recent runs and status chips.
- Run detail page with phase timeline + task table.
- Validation tab with final/per-task results and diagnostics.
- Artifact browser for key `.autodev` files.
- Run start/resume action panel.

### 8.2 Explicit MVP Exclusions

- Multi-tenant org management.
- Deep diff tooling across arbitrary large artifact sets.
- Full policy authoring UI.

---

## 9. 2–4 Week Expansion Plan

## Week 1–2 (Stabilization + operator value)

- Add run comparison view.
- Add event timeline from run trace.
- Add failure triage shortcuts and direct links.
- Harden error handling for missing/partial artifacts.

## Week 3–4 (Evaluator/developer depth)

- Add task dependency graph + skipped reason visualization.
- Add quality trend analytics across runs.
- Add saved filters/views and lightweight share links.
- Add basic RBAC management and audit log explorer.

---

## 10. Risks, Assumptions, Open Questions

## 10.1 Risks

1. **Artifact schema drift risk**
   - Internal JSON structures may evolve; GUI parsing can break without versioning.

2. **Concurrency and file-lock risk**
   - Reading artifacts while CLI writes may produce transient parse failures.

3. **Operational control risk**
   - Unsafe run controls (kill/rerun) may cause inconsistent state if not gated.

4. **Security exposure risk**
   - Raw logs/artifacts may accidentally contain sensitive environment details.

## 10.2 Assumptions

- `.autodev` artifact model remains primary system of record in near term.
- CLI remains authoritative executor; GUI is control/observability layer.
- Single-host deployment is acceptable for MVP.

## 10.3 Open Questions

1. Should GUI persist run index DB, or compute from filesystem each time?
2. What is the expected max concurrent runs per host?
3. Is operator allowed to stop/kill active process in MVP?
4. What retention/cleanup policy applies to historical run artifacts?
5. Do we need signed artifact snapshots for compliance-grade audit?
6. Which auth model is required first (local auth, OAuth, SSO)?

---

## 11. Acceptance Criteria for This Requirement Set

This requirement set is considered ready for implementation planning when:

- Stakeholders agree on P0 boundary and role permissions.
- Backend integration points are confirmed against actual `.autodev` outputs.
- MVP excludes policy/tenant complexity explicitly.
- Open questions are assigned owners and decision deadlines.
