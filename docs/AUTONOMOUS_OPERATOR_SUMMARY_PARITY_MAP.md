# Autonomous Operator Summary Parity Map (AV5-006)

Canonical operator triage summary parity across CLI/API/GUI.

## Canonical summary fields

Source of truth: `autodev.autonomous_mode.build_operator_audit_summary()`

| Field | CLI (`autodev autonomous triage-summary`) | API (`GET /api/autonomous/quality-gate/latest`) | GUI |
|---|---|---|---|
| `status` | ✅ | ✅ (`summary.status`) | ✅ |
| `preflight_status` | ✅ | ✅ (`summary.preflight_status`) | ✅ |
| `gate_counts` | ✅ | ✅ (`summary.gate_counts`) | ✅ |
| `guard_decision` | ✅ | ✅ (`summary.guard_decision`) | ✅ |
| `operator_guidance_top` | ✅ | ✅ (`summary.operator_guidance_top`) | ✅ |

## Degraded / missing-artifact behavior

Even when optional autonomous artifacts are missing, all surfaces keep the same field shape.

Representative degraded snapshot:

```json
{
  "status": "completed",
  "preflight_status": "unknown",
  "gate_counts": {"pass": 0, "fail": 0, "total": 0},
  "guard_decision": null,
  "operator_guidance_top": [
    {
      "code": "autonomous.unmapped_or_missing_code",
      "actions": ["Capture the typed code and context from artifacts, then escalate for playbook-map update."]
    }
  ]
}
```

Notes:
- API additionally exposes `warnings[]` for missing artifacts (`gate_results`, `strategy_trace`, `guard_decisions`, etc.).
- CLI `triage-summary` intentionally stays on canonical fields; use `autodev autonomous summary` for full diagnostics.

## Snapshot evidence

Fixtures:
- `autodev/tests/fixtures/autonomous_summary_parity/canonical.json`
- `autodev/tests/fixtures/autonomous_summary_parity/degraded_missing_artifacts.json`

Tests:
- CLI parity snapshots: `autodev/tests/test_autonomous_summary.py`
- API parity snapshots: `autodev/tests/test_gui_mvp_server.py`

## GUI smoke checklist

1. Open `/api/autonomous/quality-gate/latest` and verify `summary` contains the 5 canonical fields.
2. Verify GUI quality-gate panel renders those same fields.
3. Repeat with a run missing gate/guard artifacts:
   - summary field shape unchanged,
   - warnings present,
   - panel still renders (no crash/blank state).
