from __future__ import annotations

from autodev.autonomous_gate_signals import make_gate_failure_reason, normalize_validation_signals


def test_normalize_validation_signals_applies_aliases_and_status_fallbacks() -> None:
    rows = normalize_validation_signals(
        [
            {"name": "py test", "status": "PASS", "ok": None},
            {"name": "pip-audit", "returncode": 1, "stderr": "high: 2"},
            {"name": "custom-validator", "status": "skipped"},
        ]
    )

    assert [r.name for r in rows] == ["pytest", "pip_audit", "custom_validator"]
    assert [r.status for r in rows] == ["passed", "failed", "unknown"]


def test_make_gate_failure_reason_emits_typed_taxonomy_fields() -> None:
    reason = make_gate_failure_reason(
        gate="tests",
        code="tests.min_pass_rate_not_met",
        message="Pytest pass rate below configured threshold.",
        signal_source="final_validation.pytest",
        threshold={"min_pass_rate": 0.9},
        observed={"pass_rate": 0.5, "sample_size": 2},
    )

    assert reason["type"] == "quality_gate_failed"
    assert reason["taxonomy_version"] == "av2-003"
    assert reason["gate"] == "tests"
    assert reason["category"] == "reliability"
    assert reason["severity"] == "blocking"
    assert reason["retryable"] is True
    assert reason["signal_source"] == "final_validation.pytest"
