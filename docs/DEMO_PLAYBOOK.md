# Local-Simple Operator Demo Playbook

## Purpose

Provide a repeatable live demo flow for the **current** local-simple operator workflow (post NXT-001~NXT-010).

This playbook assumes:
- Demo host has this repo checked out.
- Python 3.11+ is available.
- You will run from repo root.

References:
- `README.md` (launcher + current limits)
- `docs/LOCAL_SIMPLE_MODE.md` (operator quickstart + hardened handoff)
- `docs/PLAN_NEXT_WEEK.md` (active plan)
- `docs/BACKLOG_NEXT_WEEK.md` (active backlog)

Legacy planning references (archive):
- `docs/ROADMAP_SHOWOFF.md`
- `docs/BACKLOG_SHOWOFF.md`

---

## 0) Pre-demo checklist (T-30 to T-5)

### Environment
- [ ] `python3 --version` is 3.11+
- [ ] `make` and `curl` are available in PATH
- [ ] Port `8787` is free (or pick another port)

### One-command sanity lane
- [ ] `make demo-bootstrap`
- [ ] (equivalent) `bash scripts/demo_bootstrap.sh`
- [ ] If you need the GUI to remain up for the demo: `make demo-bootstrap-serve`

### Data
- [ ] At least 3 runs exist (`ok`, `failed`, `running/partial`) under `generated_runs/`
- [ ] If local runs are missing: `python3 scripts/showoff_seed_fixtures.py --clean`
- [ ] Generate local scorecard: `make demo-scorecard`
- [ ] Confirm `artifacts/demo-day/demo_scorecard_latest.{md,json}` exists

### Server/API sanity (copy-paste)
```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/runs
curl -fsS http://127.0.0.1:8787/api/gui/context
```

Expected highlights:
- `/healthz` -> `{ "ok": true }`
- `/api/runs` -> non-empty `runs`
- `/api/gui/context` -> mode is `local-simple` or `local_simple`

---

## 1) Live script (15-minute format)

### 00:00–01:30 — Framing

Script:
- “This is the local-simple operator lane on top of AutoDev CLI artifacts.”
- “Today’s focus is deterministic operator workflow: quick run, process tracking, artifact triage.”

Expected visual:
- Dashboard with run list loaded.

### 01:30–04:30 — Dashboard + quick run

Script:
- Show run list, status chips, and metadata (profile/model/timestamps).
- Trigger **Quick Run** from Overview tab.

Action:
- Kick one quick run and show immediate process creation.

### 04:30–08:00 — Processes panel

Script:
- Explain active/recent process tracking, run linkage, retry-chain summary, transition history.
- Demonstrate stop/retry controls and idempotent behavior (NXT-009 hardening).

Action:
- Open one process row and inspect history updates.

### 08:00–11:00 — Artifact Viewer triage path

Script:
- Show failed-validator deep-link into Artifact Viewer.
- Explain pretty JSON rendering + raw fallback for malformed/truncated payloads.

Action:
- Open a failed run artifact and copy/download payload.

### 11:00–13:00 — API grounding

Script:
- Show that UI state is sourced from API + `.autodev/*` artifacts (not mocked).

Action (terminal split):
```bash
curl -fsS http://127.0.0.1:8787/api/runs | jq '.runs | length'
curl -fsS http://127.0.0.1:8787/api/processes | jq '.processes | length'
```

### 13:00–15:00 — Next-week direction

Script:
- Open active plan/backlog docs.
- Close with operator reliability priorities and known limits.

Action:
- Open `docs/PLAN_NEXT_WEEK.md` and `docs/BACKLOG_NEXT_WEEK.md`.

---

## 2) Failure response runbook

### Failure A — GUI page does not load

Checks:
1. Confirm process is running.
2. Confirm port conflict (`lsof -i :8787`).
3. Relaunch with explicit host/port.

Recovery:
- Switch to alternate port and continue:
```bash
autodev local-simple --runs-root ./generated_runs --host 127.0.0.1 --port 8788 --open
```

### Failure B — `/api/runs` returns empty

Checks:
1. Verify runs root path.
2. Verify run directories include `.autodev/*` artifacts.
3. Regenerate fixtures.

Recovery:
```bash
python3 scripts/showoff_seed_fixtures.py --clean
```

### Failure C — Start/stop/retry action fails

Checks:
1. Read status hint in UI (local-simple provides likely-fix hints).
2. Confirm target process state in Processes panel.
3. Retry action once after refresh.

Recovery:
- Use a different run/process for demo flow and keep triage for follow-up backlog item.

### Failure D — Artifact render issue on a specific run

Checks:
1. Open raw mode fallback in Artifact Viewer.
2. Validate API payload directly.

Recovery:
- Continue with a healthy run and log the problematic run id for follow-up.

---

## 3) Operator notes

- Prefer `make demo-bootstrap-serve` for predictable setup.
- Keep one terminal open for API proof (`curl`).
- Do not claim streaming updates (polling-based in MVP).

---

## 4) Post-demo capture template

Record immediately after demo:
- What worked
- What failed
- Which runbook branch was used
- New backlog tickets discovered
- Any acceptance criteria updates
