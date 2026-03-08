# Autonomous v2 Release Checklist (AV2-014)

This checklist is the minimum Go/No-Go guardrail for autonomous v2 release readiness.

## 1) Generate deterministic release evidence

```bash
make smoke-autonomous-e2e
```

Expected output:
- `[AV2-013 smoke] PASS`
- new artifact run under `artifacts/autonomous-e2e-smoke/<timestamp>/`
- `result.json` + `snapshots.json` (AV3 schema tagged with `schema_version: "av3-002-v1"`)

## 2) Verify required autonomous signals exist

```bash
make check-release-autonomous
# or
python scripts/check_release_autonomous.py --artifacts-dir ./artifacts/autonomous-e2e-smoke
```

Required evidence signals (must all be present):
- **preflight**: `snapshots.state.preflight.status == "passed"`
- **quality gate**: `snapshots.gate_results.attempts` non-empty
- **strategy trace**: `snapshots.strategy_trace.attempts` exists (AV3 schema)
- **stop-guard**: `snapshots.guard.latest.reason_code` present
- **report**: `snapshots.report` includes run/preflight/guard summary fields (AV3 schema)
- **summary**: `snapshots.summary_json` includes preflight + gate counts + guard decision
- **API smoke**: `snapshots.quality_gate_latest` not empty and includes summary guard decision

Schema behavior:
- Default mode is tolerant for legacy AV2 artifacts (missing `schema_version` emits explicit warnings).
- Use `python scripts/check_release_autonomous.py --strict-schema ...` to enforce declared AV3 schema on all evidence artifacts.

If check fails, fix the missing evidence and rerun smoke/check before release.

## 3) Full release lane

```bash
make strict
```

`make strict` now includes `check-release-autonomous`, so autonomous evidence is part of release gates.

## 4) Rollout guardrails (operator policy)

- Keep stop-guard policy enabled (do not disable repeated-failure/no-improvement stops in release profiles).
- Require operator triage for `autonomous_guard.*` stop reason codes before re-running unattended loops.
- Keep API summary endpoint (`/api/autonomous/quality-gate/latest`) available for GUI/operator parity checks.
- Treat missing autonomous smoke artifacts as **No-Go** for production release.
