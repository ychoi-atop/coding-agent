# Autonomous Failure Playbook

Operator-facing quick actions for typed autonomous failure codes.

This document is intentionally concise and linkable from:
- `AUTONOMOUS_REPORT.md`
- `.autodev/autonomous_report.json` (`operator_guidance`, `incident_routing`)
- `.autodev/autonomous_incident_packet.json` (`failure_codes`, `reproduction`, `operator_guidance.top_actions`)
- `autodev autonomous summary`

Deterministic drill scenarios for these code paths are tracked in
`docs/AUTONOMOUS_FAILURE_DRILL_PACK.md`.

Operator summary parity reference (CLI/API/GUI canonical fields + degraded behavior):
- `docs/AUTONOMOUS_OPERATOR_SUMMARY_PARITY_MAP.md`

## Routing defaults (owner/SLA/escalation)

`incident_routing` resolves typed failure codes to:
- `owner_team`
- `severity`
- `target_sla`
- `escalation_class`

Family fallback defaults:
- `tests/security/performance` (gate): Feature/Perf/Security engineering ownership, fast hotfix SLA
- `autonomous_guard.*`: Release Engineering, autonomy-control escalation
- `autonomous_preflight.*`: Platform Operations, run-configuration escalation
- `autonomous_budget_guard.*`: Release Engineering, budget-control escalation
- unknown/unmapped: Autonomy On-Call manual triage fallback

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

## Retention / compaction operations runbook (AV4-013)

Use this path when retention/compaction decisions appear in incident artifacts and an operator must recover safely without losing forensics.

Primary evidence surfaces:
- `.autodev/autonomous_incident_packet.json` → `retention_compaction.decisions[]`
- `AUTONOMOUS_REPORT.md` → Retention / Compaction Decisions section
- `autodev autonomous summary --run-dir <run_dir>` for current run status before any cleanup actions

### Recovery path checklist

1. **Freeze destructive actions**
   - Pause automated compaction/cleanup jobs for the affected run scope.
   - Keep the failed run directory and `.autodev/` artifacts immutable until triage completes.
2. **Capture decision evidence**
   - Record each retention/compaction decision + rationale link from incident packet/report.
   - Confirm whether compaction was deferred (`defer_compaction_until_recovery`) or already applied.
3. **Stabilize and validate**
   - Resolve the primary failure branch first (gate/guard/preflight/budget sections above).
   - Re-run targeted validation for the affected run and verify summary status is stable.
4. **Resume retention flow deliberately**
   - Resume compaction only after recovery validation succeeds.
   - Keep an operator note with timestamp, approver, and affected run IDs.

### Rollback steps (if retention/compaction change caused risk)

1. Stop further retention/compaction writes for the affected scope.
2. Restore archived/raw artifacts from the last known-good snapshot or backup.
3. Rebuild canonical run evidence files if needed (`autonomous_state.json`, report/summary artifacts) from restored data.
4. Re-run `autodev autonomous summary --run-dir <run_dir>` and confirm required diagnostics are present.
5. Document rollback cause, restored scope, and follow-up policy adjustment before re-enabling compaction.

### Walk-through quick check (operator dry run)

- [ ] Locate retention/compaction decisions in incident packet/report.
- [ ] Confirm compaction is paused/deferred during recovery.
- [ ] Validate recovery branch is complete and summary output is healthy.
- [ ] Execute rollback steps on paper/tabletop (or staging) for one sample run.
- [ ] Record operator sign-off before re-enabling automated compaction.

## Unknown or unmapped codes

If a code appears without an exact playbook mapping:
1. Capture the raw code + context from artifacts.
2. Follow the closest family section above (gate/guard/preflight/budget).
3. Add/update code mapping in autonomous operator guidance so future runs link exact actions.
