from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_mode as autonomous_mode  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_extract_autonomous_summary_aggregates_gate_and_strategy_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-001",
            "request_id": "req-001",
            "profile": "minimal",
            "completed_at": "2026-03-08T01:00:00Z",
            "latest_strategy": {"name": "mixed"},
            "attempts": [],
        },
    )
    _write_json(
        artifacts / "autonomous_gate_results.json",
        {
            "attempts": [
                {
                    "iteration": 1,
                    "gate_results": {
                        "passed": False,
                        "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
                    },
                },
                {
                    "iteration": 2,
                    "gate_results": {
                        "passed": False,
                        "fail_reasons": [
                            {"code": "tests.min_pass_rate_not_met"},
                            {"code": "security.max_high_findings_exceeded"},
                        ],
                    },
                },
                {
                    "iteration": 3,
                    "gate_results": {
                        "passed": True,
                        "fail_reasons": [],
                    },
                },
            ]
        },
    )
    _write_json(
        artifacts / "autonomous_strategy_trace.json",
        {
            "latest": {
                "name": "security-focused",
                "rotation_applied": True,
            }
        },
    )
    _write_json(
        artifacts / "autonomous_guard_decisions.json",
        {
            "decisions": [
                {
                    "iteration": 2,
                    "guard_decision": {
                        "decision": "stop",
                        "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                        "rollback_recommended": True,
                    },
                }
            ],
            "latest": {
                "decision": "stop",
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                "rollback_recommended": True,
            },
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["status"] == "failed"
    assert summary["gate_counts"] == {"pass": 1, "fail": 2, "total": 3}
    assert summary["dominant_fail_codes"][0] == {"code": "tests.min_pass_rate_not_met", "count": 2}
    assert summary["dominant_fail_codes"][1] == {"code": "security.max_high_findings_exceeded", "count": 1}
    assert summary["latest_strategy"]["name"] == "security-focused"
    assert summary["latest_strategy_source"] == "strategy_trace"
    assert summary["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert summary["guard_decision_source"] == "guard_decisions"
    assert summary["guard_decisions_total"] == 1
    assert summary["incident_owner_team"] == "Feature Engineering"
    assert summary["incident_severity"] == "high"
    assert summary["incident_target_sla"] == "4h"
    assert summary["incident_escalation_class"] == "engineering_hotfix"
    assert summary["warnings"] == []


def test_extract_autonomous_summary_handles_missing_or_partial_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-missing"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": True,
            "run_id": "run-missing",
            "latest_strategy": {"name": "mixed"},
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["status"] == "completed"
    assert summary["gate_counts"] == {"pass": 0, "fail": 0, "total": 0}
    assert summary["dominant_fail_codes"] == []
    assert summary["latest_strategy"]["name"] == "mixed"
    assert summary["latest_strategy_source"] == "report"
    assert summary["guard_decision"] is None
    assert summary["guard_decision_source"] == "none"
    assert summary["incident_owner_team"] == "Autonomy On-Call"
    assert summary["incident_severity"] == "medium"
    assert summary["incident_target_sla"] == "12h"
    assert summary["incident_escalation_class"] == "manual_triage"
    assert len(summary["warnings"]) == 3
    assert any("gate_results: missing" in item for item in summary["warnings"])
    assert any("strategy_trace: missing" in item for item in summary["warnings"])
    assert any("guard_decisions: missing" in item for item in summary["warnings"])


def test_autonomous_summary_cli_outputs_json_and_text(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-cli"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-cli",
            "latest_strategy": {"name": "mixed"},
            "guard_decision": {
                "decision": "stop",
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
            "guard_decisions_total": 1,
        },
    )
    _write_json(
        artifacts / "autonomous_gate_results.json",
        {
            "attempts": [
                {
                    "iteration": 1,
                    "gate_results": {
                        "passed": False,
                        "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
                    },
                }
            ]
        },
    )
    _write_json(
        artifacts / "autonomous_guard_decisions.json",
        {
            "decisions": [
                {
                    "iteration": 1,
                    "guard_decision": {
                        "decision": "stop",
                        "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                    },
                }
            ],
            "latest": {
                "decision": "stop",
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
        },
    )

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir)])
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert payload["status"] == "failed"
    assert payload["gate_counts"]["fail"] == 1
    assert payload["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert payload["operator_guidance"]["top"]

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir), "--format", "text"])
    text_out = capsys.readouterr().out
    assert "# Autonomous Run Summary" in text_out
    assert "status: failed" in text_out
    assert "incident_owner_team: Feature Engineering" in text_out
    assert "incident_target_sla: 4h" in text_out
    assert "dominant_fail_codes: tests.min_pass_rate_not_met(1)" in text_out
    assert "guard_decision: stop (autonomous_guard.repeated_gate_failure_limit_reached)" in text_out
    assert "operator_guidance_top:" in text_out
    assert "incident_routing_top:" in text_out
    assert "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md" in text_out


def test_extract_autonomous_summary_builds_operator_guidance_with_fallbacks(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-guidance-fallback"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-guidance-fallback",
            "preflight": {
                "status": "failed",
                "reason_codes": ["autonomous_preflight.path_not_allowlisted"],
            },
        },
    )
    _write_json(
        artifacts / "autonomous_gate_results.json",
        {
            "attempts": [
                {
                    "iteration": 1,
                    "gate_results": {
                        "passed": False,
                        "fail_reasons": [{"code": "custom.operator_code"}],
                    },
                }
            ]
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    guidance = summary["operator_guidance"]
    assert guidance["taxonomy_version"] == "av2-011"
    assert any(item["code"] == "autonomous_preflight.path_not_allowlisted" for item in guidance["resolved"])
    assert any(
        item["code"] == "custom.operator_code" and item["source"] == "generic_fallback"
        for item in guidance["resolved"]
    )

    routing = summary["incident_routing"]
    assert routing["taxonomy_version"] == "av3-004-v1"
    assert any(item["code"] == "autonomous_preflight.path_not_allowlisted" for item in routing["resolved"])
    assert any(item["code"] == "custom.operator_code" and item["source"] == "generic_fallback" for item in routing["resolved"])
    assert summary["incident_owner_team"] == "Platform Operations"



def test_extract_autonomous_summary_surfaces_budget_guard_outcome(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-budget-guard"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-budget-guard",
            "budget_guard": {
                "status": "triggered",
                "decision": {
                    "decision": "stop",
                    "reason_code": "autonomous_budget_guard.max_autonomous_iterations_reached",
                },
                "diagnostics": [
                    {
                        "reason_code": "autonomous_budget_guard.estimated_token_budget_not_available",
                    }
                ],
            },
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["budget_guard_status"] == "triggered"
    assert summary["budget_guard_decision"]["reason_code"] == "autonomous_budget_guard.max_autonomous_iterations_reached"
    assert "autonomous_budget_guard.estimated_token_budget_not_available" in summary["budget_guard_reason_codes"]

    rendered = autonomous_mode._render_autonomous_summary_text(summary)
    assert "budget_guard: triggered" in rendered
    assert "budget_guard_decision: stop (autonomous_budget_guard.max_autonomous_iterations_reached)" in rendered


def test_extract_autonomous_summary_exposes_preflight_status_and_reason_codes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-preflight"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-preflight",
            "preflight": {
                "status": "failed",
                "ok": False,
                "reason_codes": [
                    "autonomous_preflight.path_blocked",
                    "autonomous_preflight.required_file_missing",
                ],
                "diagnostics": [
                    {
                        "code": "preflight.path.matches_blocked_path",
                        "reason_code": "autonomous_preflight.path_blocked",
                        "message": "path 'prd' matches a blocked path",
                        "severity": "error",
                        "retryable": False,
                    }
                ],
            },
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["preflight_status"] == "failed"
    assert "autonomous_preflight.path_blocked" in summary["preflight_reason_codes"]
    assert any(
        isinstance(item, dict) and item.get("reason_code") == "autonomous_preflight.path_blocked"
        for item in summary["diagnostics"]
    )

    rendered = autonomous_mode._render_autonomous_summary_text(summary)
    assert "preflight: failed" in rendered
    assert "preflight_reason_codes: autonomous_preflight.path_blocked,autonomous_preflight.required_file_missing" in rendered


def test_extract_autonomous_summary_exposes_typed_resume_diagnostics(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-resume-diag"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-resume-diag",
            "latest_strategy": {"name": "mixed"},
            "resume_diagnostics": [
                {
                    "type": "autonomous_resume_diagnostic",
                    "taxonomy_version": "av2-008",
                    "code": "resume.state.invalid_json",
                    "message": "state file is not valid JSON; recovery fallback will be used",
                    "severity": "warning",
                    "recovered": True,
                }
            ],
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["resume_diagnostics"][0]["code"] == "resume.state.invalid_json"
    assert any(isinstance(item, dict) and item.get("code") == "resume.state.invalid_json" for item in summary["diagnostics"])
    assert any(isinstance(item, dict) and item.get("artifact") == "gate_results" for item in summary["diagnostics"])

    rendered = autonomous_mode._render_autonomous_summary_text(summary)
    assert "Resume diagnostics:" in rendered
    assert "resume.state.invalid_json" in rendered
