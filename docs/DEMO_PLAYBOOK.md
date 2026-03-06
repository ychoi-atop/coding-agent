# Showoff Live Demo Playbook

## Purpose

Provide a repeatable live demo flow for the current GUI + API baseline, while clearly handling failure paths.

This playbook assumes:
- Demo host has this repo checked out.
- Python environment is available.
- Demo data is either real runs under `generated_runs/` or fixture runs created by script.

References:
- `README.md` (GUI launcher and known limits)
- `docs/ROADMAP_SHOWOFF.md`
- `docs/BACKLOG_SHOWOFF.md`

---

## 0) Pre-Demo Checklist (T-30 to T-5 min)

### Environment
- [ ] `python --version` is 3.11+
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Port `8787` is free (or decide alternate port)

### Data
- [ ] At least 3 runs available (`ok`, `failed`, `running/partial`) in runs root
- [ ] If local runs are missing, generate fixtures: `python3 scripts/showoff_seed_fixtures.py --clean`

### Server sanity
- [ ] `autodev gui --runs-root ./generated_runs --host 127.0.0.1 --port 8787`
- [ ] `curl http://127.0.0.1:8787/healthz` returns `{ "ok": true }`
- [ ] `curl http://127.0.0.1:8787/api/runs` returns non-empty list

### Backup assets
- [ ] Keep one screenshot of dashboard and run detail as fallback
- [ ] Keep one pre-recorded 60s terminal output snippet as hard fallback

---

## 1) Live Script (15-minute format)

## 00:00–01:30 — Framing (What this is, what it is not)

Script:
- “This is a GUI layer over the existing AutoDev CLI pipeline.”
- “Today’s focus is observability of run artifacts and baseline operator workflows.”
- “Current MVP is intentionally limited: read-heavy with no full lifecycle control yet.”

Expected visual:
- Dashboard with run list loaded.

## 01:30–04:30 — Dashboard walkthrough

Script:
- Show run list and status chips.
- Explain project/profile/model metadata fields.
- Point out last-updated timestamps and practical use during triage.

Action:
- Click 2–3 runs with different statuses.

## 04:30–08:00 — Run detail deep dive

Script:
- Show phase timeline and task list.
- Show blocker list and explain unresolved blockers.
- Show validation section and explain pass/fail signals.

Action:
- Open a failed run and trace one validator failure to context.

## 08:00–11:00 — Artifact and API reality check

Script:
- Explain that data comes directly from `.autodev/*` artifacts.
- Show `curl /api/runs` and one `/api/runs/<id>` response.
- Clarify this keeps the system grounded in existing CLI outputs.

Action:
- Terminal split-screen with browser.

## 11:00–13:00 — Planned near-term upgrade path

Script:
- Summarize P0 priorities: status hardening, start/resume API wiring, audit persistence, demo harness.
- Emphasize no engine rewrite in P0.

Action:
- Open `docs/ROADMAP_SHOWOFF.md` and `docs/BACKLOG_SHOWOFF.md` briefly.

## 13:00–15:00 — Q&A / realistic constraints

Script:
- State known limits directly:
  - no streaming updates
  - no stop/kill/retry controls
  - schema versioning not stabilized yet
- Close with immediate next tickets (Top 5).

---

## 2) Failure Response Runbook

## Failure A — GUI page does not load

### Symptoms
- Browser cannot open `http://127.0.0.1:8787`

### Checks
1. Confirm process is running.
2. Confirm port conflict (`lsof -i :8787`).
3. Relaunch with explicit host/port.

### Recovery action
- Switch to alternate port (e.g., `8788`) and continue.
- If still failing, continue demo via API-only terminal flow.

---

## Failure B — `/api/runs` returns empty

### Symptoms
- Dashboard shows “No runs found”.

### Checks
1. Verify runs root path.
2. Verify run directories include `.autodev/*` artifacts.
3. Generate fixture dataset.

### Recovery action
- Run fixture setup script.
- Refresh UI and proceed with fixture runs.

---

## Failure C — JSON parse or detail API error for one run

### Symptoms
- Selecting specific run fails with parse/missing artifact issue.

### Checks
1. Inspect problematic run’s `.autodev` files.
2. Pick alternate run to continue live flow.

### Recovery action
- Continue with healthy run.
- Use failure as proof-point for P0 hardening ticket (SHW-002).

---

## Failure D — Slow or frozen UI during detail render

### Symptoms
- Run detail panel hangs on large trace/validation payload.

### Checks
1. Browser console errors.
2. API response size and latency in network tab.

### Recovery action
- Reload page and pick smaller run.
- Fall back to terminal `curl` + artifact file inspection.

---

## Failure E — Live start/resume control unavailable (expected in current MVP)

### Symptoms
- Audience asks for full operator controls not yet implemented.

### Response script
- “That control plane is deliberately in P0 implementation scope next; current build is observability-first.”
- Show relevant tickets (SHW-003/004/006) and acceptance criteria.

---

## 3) Demo Operator Notes

- Avoid claiming “real-time” behavior until streaming is implemented.
- Prefer deterministic fixture-backed demo when network/provider is unstable.
- Keep one terminal visible to prove API grounding and avoid “mock-only” perception.

---

## 4) Post-Demo Capture Template

Record immediately after demo:
- What worked
- What failed
- Which runbook branch was used
- New backlog tickets discovered
- Any acceptance criteria updates
