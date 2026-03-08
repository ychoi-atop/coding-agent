# Autonomous Failure Playbook

Operator-facing quick actions for typed autonomous failure codes.

This document is intentionally concise and linkable from:
- `AUTONOMOUS_REPORT.md`
- `.autodev/autonomous_report.json` (`operator_guidance`)
- `autodev autonomous summary`

## Gate failures

Typical code prefix/domain:
- `tests.*`
- `security.*`
- `performance.*`

Operator actions:
1. Inspect latest gate diagnostics (`.autodev/autonomous_gate_results.json`) and isolate the highest-confidence blocker.
2. Apply focused remediation (tests/security/perf), then rerun validation checks.
3. Resume autonomous retries only after the gate signal is measurably improved.

## Guard stops

Typical code prefix:
- `autonomous_guard.*`

Operator actions:
1. Treat guard stop as a hard intervention point (do not blind-retry).
2. Review repeated/no-improvement attempt patterns in `AUTONOMOUS_REPORT.md` and `.autodev/autonomous_guard_decisions.json`.
3. Decide one path: rollback, narrower scope, or revised strategy; then resume deliberately.

## Preflight failures

Typical code prefix:
- `autonomous_preflight.*`

Operator actions:
1. Fix workspace policy/prerequisite issues (allowlist/blocked paths, required file access, artifact writability).
2. Re-run preflight (`autodev autonomous start ...`) and confirm preflight status is `passed`.
3. Start unattended loop only after preflight is clean.

## Budget-guard stops

Typical code prefix:
- `autonomous_budget_guard.*`

Operator actions:
1. Inspect whether stop came from wall-clock or iteration cap.
2. Re-scope objective before increasing budgets.
3. Increase guard limits only with explicit operator approval and rationale.

## Unknown or unmapped codes

If a code appears without an exact playbook mapping:
1. Capture the raw code + context from artifacts.
2. Follow the closest family section above (gate/guard/preflight/budget).
3. Add/update code mapping in autonomous operator guidance so future runs link exact actions.
