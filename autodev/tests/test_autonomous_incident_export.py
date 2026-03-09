from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_mode as autonomous_mode  # noqa: E402
from autodev.autonomous_incident_export import render_incident_export  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sample_packet() -> dict:
    return {
        "schema_version": "av5-005-v2",
        "status": "failed",
        "run_summary": {
            "run_id": "run-123",
            "request_id": "req-123",
            "profile": "enterprise",
            "failure_reason": "autonomous_guard_stop",
            "iterations_total": 3,
            "iterations_failed": 3,
            "completed_at": "2026-03-08T11:11:00Z",
        },
        "failure_codes": {
            "typed_codes": ["tests.min_pass_rate_not_met", "security.max_high_findings_exceeded"],
            "root_cause_codes": ["tests.min_pass_rate_not_met"],
        },
        "incident_routing": {
            "primary": {
                "owner_team": "Feature Engineering",
                "severity": "high",
                "target_sla": "4h",
                "escalation_class": "engineering_hotfix",
            }
        },
        "reproduction": {
            "run_dir": "/tmp/generated_runs/run-123",
            "artifact_paths": {
                "state": ".autodev/autonomous_state.json",
                "report_json": ".autodev/autonomous_report.json",
                "incident_packet": ".autodev/autonomous_incident_packet.json",
            },
        },
        "operator_guidance": {
            "playbook": "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md",
            "top_actions": [
                {
                    "code": "tests.min_pass_rate_not_met",
                    "title": "Tests gate failed",
                    "action": "Inspect failing tests first",
                    "playbook_url": "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md#gate-failures",
                }
            ],
        },
        "retention_decisions": {
            "decision_version": "av4-009-v1",
            "decisions": [
                {
                    "category": "retention",
                    "decision": "retain_full_incident_artifacts",
                    "rationale": "Keep complete incident artifacts during active triage.",
                },
                {
                    "category": "compaction",
                    "decision": "defer_compaction_until_recovery",
                    "rationale": "Avoid losing evidence while incident is unresolved.",
                },
            ],
            "rationale_links": [
                "docs/AUTONOMOUS_V4_WAVE_PLAN.md#risks",
                "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md#gate-failures",
            ],
        },
        "generated_at": "2026-03-08T11:11:01Z",
    }


def test_render_incident_export_formats() -> None:
    packet = _sample_packet()

    slack_text = render_incident_export(packet, "slack")
    assert ":rotating_light: *AutoDev Incident Packet*" in slack_text
    assert "*Run:* run-123 (request req-123)" in slack_text
    assert "*Routing:* Feature Engineering | severity=high | SLA=4h | escalation=engineering_hotfix" in slack_text
    assert "• 1. [tests.min_pass_rate_not_met] Tests gate failed" in slack_text
    assert "*Retention decisions:* version=av4-009-v1" in slack_text
    assert "• 1. [retention] retain_full_incident_artifacts" in slack_text

    markdown_text = render_incident_export(packet, "markdown")
    assert "# Autonomous Incident Brief" in markdown_text
    assert "- Run ID: `run-123`" in markdown_text
    assert "- Owner Team: **Feature Engineering**" in markdown_text
    assert "## Top Operator Actions" in markdown_text
    assert "## Retention / Compaction Decisions" in markdown_text
    assert "- Decision Schema: `av4-009-v1`" in markdown_text

    email_text = render_incident_export(packet, "email")
    assert "Subject: [AutoDev Incident] run-123 (high)" in email_text
    assert "An autonomous run failed and generated an incident packet." in email_text
    assert "- Owner Team: Feature Engineering" in email_text
    assert "Top Actions" in email_text
    assert "Retention / Compaction Decisions" in email_text
    assert "- Decision Schema: av4-009-v1" in email_text


def test_autonomous_incident_export_cli_outputs_formatted_text(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-incident-export"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    autonomous_mode.cli(["incident-export", "--run-dir", str(run_dir), "--format", "markdown"])
    out = capsys.readouterr().out

    assert "# Autonomous Incident Brief" in out
    assert "- Run ID: `run-123`" in out
    assert "- Typed Codes: tests.min_pass_rate_not_met, security.max_high_findings_exceeded" in out
    assert "## Retention / Compaction Decisions" in out


def test_autonomous_incident_export_cli_handles_missing_packet(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-no-packet"
    run_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        autonomous_mode.cli(["incident-export", "--run-dir", str(run_dir), "--format", "slack"])

    message = str(exc.value)
    assert "incident packet not found" in message
    assert ".autodev/autonomous_incident_packet.json" in message
    assert "autodev autonomous summary --run-dir <path>" in message
