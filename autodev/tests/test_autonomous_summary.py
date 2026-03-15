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




def _load_parity_snapshot_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parent / "fixtures" / "autonomous_summary_parity"
    return json.loads((root / f"{name}.json").read_text(encoding="utf-8"))

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
    _write_json(
        artifacts / "autonomous_incident_packet.json",
        {
            "schema_version": "av3-005-v1",
            "status": "failed",
            "run_summary": {"run_id": "run-001"},
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
    assert summary["incident_packet"]["status"] == "ok"
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
    assert summary["incident_packet"]["status"] == "not_generated"
    assert len(summary["warnings"]) == 3
    assert any("gate_results: missing" in item for item in summary["warnings"])
    assert any("strategy_trace: missing" in item for item in summary["warnings"])
    assert any("guard_decisions: missing" in item for item in summary["warnings"])


def test_extract_autonomous_summary_warns_when_failed_run_missing_incident_packet(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-missing-packet"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-missing-packet",
            "latest_strategy": {"name": "mixed"},
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["status"] == "failed"
    assert summary["incident_packet"]["status"] == "missing"
    assert any("incident_packet: missing" in item for item in summary["warnings"])


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
    _write_json(
        artifacts / "autonomous_incident_packet.json",
        {
            "schema_version": "av3-005-v1",
            "status": "failed",
            "run_summary": {"run_id": "run-cli"},
        },
    )
    (artifacts / "autonomous_ticket_draft.md").write_text("# draft", encoding="utf-8")
    _write_json(
        artifacts / "autonomous_ticket_draft.json",
        {
            "title": "[AutoDev][high] tests.min_pass_rate_not_met on run-cli",
            "severity": "high",
            "owner_team": "Feature Engineering",
            "target_sla": "4h",
        },
    )

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir)])
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert payload["status"] == "failed"
    assert payload["gate_counts"]["fail"] == 1
    assert payload["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert payload["incident_packet"]["status"] == "ok"
    assert payload["ticket_draft"]["markdown"]["status"] == "ok"
    assert payload["ticket_draft"]["json"]["status"] == "ok"
    assert payload["operator_guidance"]["top"]

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir), "--format", "text"])
    text_out = capsys.readouterr().out
    assert "# Autonomous Run Summary" in text_out
    assert "status: failed" in text_out
    assert "incident_owner_team: Feature Engineering" in text_out
    assert "incident_target_sla: 4h" in text_out
    assert "incident_packet: ok" in text_out
    assert "ticket_draft_markdown: ok" in text_out
    assert "ticket_draft_json: ok" in text_out
    assert "dominant_fail_codes: tests.min_pass_rate_not_met(1)" in text_out
    assert "guard_decision: stop (autonomous_guard.repeated_gate_failure_limit_reached)" in text_out
    assert "operator_guidance_top:" in text_out
    assert "incident_routing_top:" in text_out
    assert "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md" in text_out


def test_build_operator_audit_summary_extracts_canonical_fields() -> None:
    snapshot = {
        "status": "failed",
        "preflight_status": "passed",
        "gate_counts": {"pass": 0, "fail": 1, "total": 1},
        "guard_decision": {"decision": "stop", "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached"},
        "operator_guidance": {"top": [{"code": "tests.min_pass_rate_not_met", "actions": ["Fix tests"]}]},
        "extra": {"ignored": True},
    }

    summary = autonomous_mode.build_operator_audit_summary(snapshot)

    assert summary == {
        "status": "failed",
        "preflight_status": "passed",
        "gate_counts": {"pass": 0, "fail": 1, "total": 1},
        "guard_decision": {"decision": "stop", "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached"},
        "operator_guidance_top": [{"code": "tests.min_pass_rate_not_met", "actions": ["Fix tests"]}],
    }


def test_autonomous_triage_summary_cli_outputs_json_and_text(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-triage-cli"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-triage-cli",
            "preflight": {"status": "passed", "reason_codes": []},
            "guard_decision": {
                "decision": "stop",
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
            "operator_guidance": {
                "top": [
                    {
                        "code": "tests.min_pass_rate_not_met",
                        "actions": ["Stabilize failing tests before retry."],
                    }
                ]
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
                        "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
                    },
                }
            ]
        },
    )

    autonomous_mode.cli(["triage-summary", "--run-dir", str(run_dir)])
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert payload["status"] == "failed"
    assert payload["preflight_status"] == "passed"
    assert payload["gate_counts"] == {"pass": 0, "fail": 1, "total": 1}
    assert payload["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert payload["operator_guidance_top"][0]["code"] == "tests.min_pass_rate_not_met"

    autonomous_mode.cli(["triage-summary", "--run-dir", str(run_dir), "--format", "text"])
    text_out = capsys.readouterr().out
    assert "# Autonomous Operator Triage Summary" in text_out
    assert "status: failed" in text_out
    assert "preflight_status: passed" in text_out
    assert "gate_counts: pass=0, fail=1, total=1" in text_out
    assert "guard_decision: stop (autonomous_guard.repeated_gate_failure_limit_reached)" in text_out
    assert "tests.min_pass_rate_not_met" in text_out




def test_autonomous_triage_summary_cli_json_matches_canonical_snapshot_fixture(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-triage-cli-snapshot"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-triage-cli-snapshot",
            "preflight": {"status": "passed", "reason_codes": []},
            "guard_decision": {
                "decision": "stop",
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
            "operator_guidance": {
                "top": [
                    {
                        "code": "tests.min_pass_rate_not_met",
                        "actions": ["Stabilize failing tests before retry."],
                    }
                ]
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
                        "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
                    },
                }
            ]
        },
    )

    autonomous_mode.cli(["triage-summary", "--run-dir", str(run_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert payload == _load_parity_snapshot_fixture("canonical")


def test_autonomous_triage_summary_cli_json_matches_degraded_snapshot_fixture(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-triage-cli-degraded"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": True,
            "run_id": "run-triage-cli-degraded",
            "latest_strategy": {"name": "mixed"},
        },
    )

    autonomous_mode.cli(["triage-summary", "--run-dir", str(run_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert payload == _load_parity_snapshot_fixture("degraded_missing_artifacts")


def test_compare_snapshots_cli_supports_operator_management_workflow(tmp_path: Path, capsys) -> None:
    runs_root = tmp_path / "runs-root"
    export_path = tmp_path / "compare-export.json"
    export_path.write_text(
        json.dumps(
            {
                "snapshot": {
                    "schema_version": "compare-trust-snapshot-v1",
                    "generated_at": "2026-03-15T12:00:00Z",
                    "source": "cli-test",
                    "left": {
                        "run_id": "run-a",
                        "status": "failed",
                        "trust": {"status": "low", "score": 0.42, "requires_human_review": True},
                    },
                    "right": {
                        "run_id": "run-b",
                        "status": "ok",
                        "trust": {"status": "high", "score": 0.96, "requires_human_review": False},
                    },
                    "delta": {"trust_status_changed": True, "trust_score": 0.54},
                    "trust_packet_diff": [{"path": "trust_signals.overall.status", "left": "low", "right": "high"}],
                    "highlights": ["Trust: low (0.42) -> high (0.96)"],
                },
                "markdown": "# Compare Trust Snapshot\n\n- baseline_run: run-a\n- candidate_run: run-b\n",
                "compare_payload": {
                    "left": {
                        "run_id": "run-a",
                        "status": "failed",
                        "trust": {"status": "low", "score": 0.42, "requires_human_review": True},
                        "trust_packet": {"trust_signals": {"overall": {"status": "low", "score": 0.42}}},
                    },
                    "right": {
                        "run_id": "run-b",
                        "status": "ok",
                        "trust": {"status": "high", "score": 0.96, "requires_human_review": False},
                        "trust_packet": {"trust_signals": {"overall": {"status": "high", "score": 0.96}}},
                    },
                    "delta": {"trust_status_changed": True, "trust_score": 0.54},
                },
            }
        ),
        encoding="utf-8",
    )

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "import",
            "--runs-root",
            str(runs_root),
            "--file",
            str(export_path),
            "--display-name",
            "Release compare",
            "--pinned",
            "true",
            "--tags",
            "release,gate",
        ]
    )
    import_payload = json.loads(capsys.readouterr().out)
    snapshot = import_payload["snapshot"]
    snapshot_id = snapshot["snapshot_id"]
    assert snapshot["display_name"] == "Release compare"
    assert snapshot["pinned"] is True
    assert snapshot["tags"] == ["release", "gate"]

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "list",
            "--runs-root",
            str(runs_root),
            "--query",
            "release",
            "--format",
            "text",
        ]
    )
    list_text = capsys.readouterr().out
    assert "# Compare Snapshots" in list_text
    assert snapshot_id in list_text
    assert "trust_delta=+0.54" in list_text

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "show",
            "--runs-root",
            str(runs_root),
            "--snapshot-id",
            snapshot_id,
        ]
    )
    detail_payload = json.loads(capsys.readouterr().out)
    assert detail_payload["snapshot"]["snapshot_id"] == snapshot_id
    assert detail_payload["compare_snapshot"]["left"]["run_id"] == "run-a"
    assert detail_payload["snapshot"]["integrity_ok"] is True
    assert detail_payload["integrity"]["mismatches"] == []

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "update",
            "--runs-root",
            str(runs_root),
            "--snapshot-id",
            snapshot_id,
            "--display-name",
            "Renamed compare",
            "--archived",
            "true",
            "--tags",
            "release,renamed",
        ]
    )
    update_payload = json.loads(capsys.readouterr().out)
    assert update_payload["snapshot"]["display_name"] == "Renamed compare"
    assert update_payload["snapshot"]["archived"] is True
    assert update_payload["snapshot"]["tags"] == ["release", "renamed"]

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "retention",
            "--runs-root",
            str(runs_root),
            "--keep-latest",
            "0",
            "--include-archived",
            "--format",
            "text",
        ]
    )
    retention_text = capsys.readouterr().out
    assert "# Compare Snapshot Retention" in retention_text
    assert snapshot_id in retention_text

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "retention",
            "--runs-root",
            str(runs_root),
            "--keep-latest",
            "0",
            "--include-archived",
            "--apply",
        ]
    )
    retention_payload = json.loads(capsys.readouterr().out)
    assert retention_payload["dry_run"] is False
    assert retention_payload["deleted_snapshot_ids"] == [snapshot_id]

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "import",
            "--runs-root",
            str(runs_root),
            "--file",
            str(export_path),
        ]
    )
    second_import = json.loads(capsys.readouterr().out)
    second_snapshot_id = second_import["snapshot"]["snapshot_id"]

    autonomous_mode.cli(
        [
            "compare-snapshots",
            "delete",
            "--runs-root",
            str(runs_root),
            "--snapshot-id",
            second_snapshot_id,
            "--format",
            "text",
        ]
    )
    delete_text = capsys.readouterr().out
    assert f"deleted compare snapshot: {second_snapshot_id}" in delete_text

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
