# Local Simple Mode

`local-simple` is a low-friction GUI/run-control mode for **single-user laptop usage**.

It is designed for quick iteration with safe localhost defaults, while keeping hardened paths (`autodev gui` + token/session policy) fully intact.

## Quickstart

```bash
# 1) Start GUI in local-simple mode
autodev local-simple --runs-root ./generated_runs

# Optional: auto-open browser on startup
autodev local-simple --runs-root ./generated_runs --open
```

Recommended run profile for this mode:

```bash
autodev --prd examples/PRD.md --out ./generated_runs --profile local_simple
```

## What Local Simple Mode changes

- Binds to `127.0.0.1` by default (localhost-first assumption)
- Sets GUI role default to `developer` (mutating actions available without extra auth config)
- Disables auth-config lookup by default unless explicitly provided
- GUI defaults are tuned for local workflow:
  - profile hint: `local_simple`
  - output root hint: `generated_runs`
  - PRD hint: `examples/PRD.md` (if present)
  - one-screen run controls for start/resume/stop/retry + **Quick Run** preset button
- Optional `--open` flag launches the default browser to the GUI URL on startup (best-effort, non-fatal if unavailable)

## One-command local workflow

- Start GUI + controls in one command:
  - `autodev local-simple --runs-root ./generated_runs`
  - `autodev local-simple --runs-root ./generated_runs --open` (best-effort browser auto-open)
- In GUI Overview tab, use **Run Controls**:
  - **Quick Run**: one-click `POST /api/runs/start` with `execute=true` and local-simple defaults (`profile/out` + selected/default PRD path)
  - Start: `POST /api/runs/start`
  - Resume: `POST /api/runs/resume`
  - Stop: `POST /api/runs/stop`
  - Retry: `POST /api/runs/retry` (`process_id` or selected `run_id` fallback)
- On mutating-action failures, the status area shows concise **Likely fixes** (deterministic rule-based hints).
- Compare + Trends are available in the Compare tab.

## When to switch to hardened mode

Use hardened mode (`autodev gui`) when any of these are true:

- Multi-user or shared environment
- Non-localhost exposure (remote host/VPN/reverse-proxy)
- Project/environment-scoped role policy required
- Audit/compliance expectations require explicit token/session controls

Hardened mode example:

```bash
AUTODEV_GUI_AUTH_CONFIG=./auth.json \
AUTODEV_GUI_ROLE=evaluator \
autodev gui --runs-root ./generated_runs --host 127.0.0.1 --port 8787
```

## Safety note

Local simple mode is intentionally optimized for laptop-local usage. It should not be treated as a production security profile.