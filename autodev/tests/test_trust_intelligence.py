from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for name in list(sys.modules):
    if name == "autodev" or name.startswith("autodev."):
        sys.modules.pop(name, None)

import autodev.autonomous_mode as autonomous_mode  # noqa: E402
from autodev.trust_intelligence import (  # noqa: E402
    build_trust_intelligence_packet,
    persist_trust_intelligence_artifacts,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_completed_run(run_dir: Path) -> None:
    artifacts = run_dir / ".autodev"
    _write_json(
        artifacts / "autonomous_report.json",
        {
            "schema_version": "av3-002-v1",
            "ok": True,
            "run_id": "run-trust",
            "request_id": "req-trust",
            "profile": "enterprise",
            "completed_at": "2026-03-15T00:00:00Z",
            "preflight": {"status": "passed", "reason_codes": []},
            "guard_decision": None,
            "operator_guidance": {
                "top": [
                    {
                        "code": "autonomous.unmapped_or_missing_code",
                        "actions": ["Review summary artifacts before approval."],
                    }
                ]
            },
            "incident_routing": {
                "primary": {
                    "owner_team": "Autonomy On-Call",
                    "severity": "medium",
                    "target_sla": "12h",
                    "escalation_class": "manual_triage",
                },
                "top": [
                    {
                        "code": "autonomous.unmapped_or_missing_code",
                        "owner_team": "Autonomy On-Call",
                        "severity": "medium",
                        "target_sla": "12h",
                        "escalation_class": "manual_triage",
                    }
                ],
            },
            "gate_results": {
                "passed": True,
                "gates": {
                    "composite_quality": {
                        "status": "passed",
                        "composite_score": 96.0,
                        "hard_blocked": False,
                        "components": {
                            "tests": 100.0,
                            "lint": 100.0,
                            "type_health": 90.0,
                            "security": 90.0,
                            "simplicity": 100.0,
                        },
                    }
                },
                "fail_reasons": [],
            },
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
                        "passed": True,
                        "gates": {
                            "composite_quality": {
                                "status": "passed",
                                "composite_score": 96.0,
                                "hard_blocked": False,
                                "components": {"tests": 100.0},
                            }
                        },
                        "fail_reasons": [],
                    },
                }
            ]
        },
    )
    _write_json(
        artifacts / "autonomous_strategy_trace.json",
        {"latest": {"name": "mixed", "rotation_applied": False}},
    )
    _write_json(
        artifacts / "autonomous_guard_decisions.json",
        {
            "schema_version": "av3-002-v1",
            "decisions": [],
            "latest": None,
        },
    )
    _write_json(
        artifacts / "run_trace.json",
        {
            "run_id": "run-trust",
            "request_id": "req-trust",
            "profile": "enterprise",
            "events": [
                {"event_type": "quality_score.computed"},
                {"event_type": "experiment.decision"},
            ],
            "phases": [{"phase": "planning", "duration_ms": 1200}],
            "llm_metrics": {
                "planner": {
                    "call_count": 2,
                    "retry_count": 1,
                }
            },
        },
    )
    _write_json(
        artifacts / "run_metadata.json",
        {
            "result_ok": True,
            "llm_usage": {"total_calls": 2},
            "autonomous_latest_strategy": {"name": "mixed"},
        },
    )
    _write_jsonl(
        artifacts / "experiment_log.jsonl",
        [
            {
                "task_id": "task-1",
                "iteration": 1,
                "attempt": 1,
                "composite_score": 96.0,
                "hard_blocked": False,
                "decision": {"decision": "accepted", "reason_code": "initial_attempt"},
                "components": {"tests": 100.0},
                "validators_failed": [],
            }
        ],
    )


def _seed_failed_review_run(run_dir: Path) -> None:
    artifacts = run_dir / ".autodev"
    _write_json(
        artifacts / "autonomous_report.json",
        {
            "schema_version": "av3-002-v1",
            "ok": False,
            "run_id": "run-failed",
            "request_id": "req-failed",
            "profile": "enterprise",
            "completed_at": "2026-03-15T00:00:00Z",
            "status": "failed",
            "preflight": {"status": "passed", "reason_codes": []},
            "guard_decision": {"decision": "stop", "reason_code": "quality_gate_failed"},
            "operator_guidance": {
                "top": [
                    {
                        "code": "quality_gate_failed",
                        "actions": ["Review failing validators and incident packet before retry."],
                    }
                ]
            },
            "incident_routing": {
                "primary": {
                    "owner_team": "Autonomy On-Call",
                    "severity": "high",
                    "target_sla": "1h",
                    "escalation_class": "manual_triage",
                }
            },
            "gate_results": {
                "passed": False,
                "gates": {
                    "composite_quality": {
                        "status": "failed",
                        "composite_score": 42.0,
                        "hard_blocked": True,
                        "components": {
                            "tests": 30.0,
                            "lint": 40.0,
                        },
                    }
                },
                "fail_reasons": [{"code": "tests_failed"}],
            },
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
                        "gates": {
                            "composite_quality": {
                                "status": "failed",
                                "composite_score": 42.0,
                                "hard_blocked": True,
                                "components": {"tests": 30.0},
                            }
                        },
                        "fail_reasons": [{"code": "tests_failed"}],
                    },
                }
            ]
        },
    )
    _write_json(
        artifacts / "autonomous_guard_decisions.json",
        {
            "schema_version": "av3-002-v1",
            "decisions": [{"decision": "stop", "reason_code": "quality_gate_failed"}],
            "latest": {"decision": "stop", "reason_code": "quality_gate_failed"},
        },
    )
    _write_json(
        artifacts / "run_trace.json",
        {
            "run_id": "run-failed",
            "request_id": "req-failed",
            "profile": "enterprise",
            "events": [{"event_type": "quality_score.computed"}],
            "phases": [{"phase": "final_validation", "duration_ms": 2500}],
            "llm_metrics": {
                "validator": {
                    "call_count": 1,
                    "retry_count": 0,
                }
            },
        },
    )
    _write_json(
        artifacts / "run_metadata.json",
        {
            "result_ok": False,
            "llm_usage": {"total_calls": 1},
        },
    )
    _write_jsonl(
        artifacts / "experiment_log.jsonl",
        [
            {
                "task_id": "task-1",
                "iteration": 1,
                "attempt": 1,
                "composite_score": 42.0,
                "hard_blocked": True,
                "decision": {"decision": "reverted", "reason_code": "quality_gate_failed"},
                "components": {"tests": 30.0},
                "validators_failed": ["pytest"],
            }
        ],
    )


def test_build_trust_intelligence_packet_from_runtime_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-trust"
    _seed_completed_run(run_dir)

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))
    packet = build_trust_intelligence_packet(run_dir, summary=summary)

    assert packet["schema_version"] == "av3-trust-v1"
    assert packet["status"] == "completed"
    assert packet["trust_signals"]["overall"]["status"] == "high"
    assert packet["latest_quality"]["source"] == "report.gate_results.composite_quality"
    assert packet["latest_quality"]["composite_score"] == 96.0
    assert packet["runtime_observability"]["event_count"] == 2
    assert packet["runtime_observability"]["llm_call_count"] == 2
    assert packet["operator_next"]["owner_team"] == "Autonomy On-Call"
    assert any(item["name"] == "run_trace" and item["sha256"] for item in packet["artifacts"]["refs"])


def test_persist_trust_intelligence_artifacts_writes_trust_and_xai_packets(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-persist"
    _seed_completed_run(run_dir)
    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))
    packet = build_trust_intelligence_packet(run_dir, summary=summary)

    paths = persist_trust_intelligence_artifacts(run_dir, packet)

    trust_json = Path(paths["trust_json"])
    xai_json = Path(paths["xai_json"])
    assert trust_json.exists()
    assert xai_json.exists()
    trust_payload = json.loads(trust_json.read_text(encoding="utf-8"))
    xai_payload = json.loads(xai_json.read_text(encoding="utf-8"))
    assert trust_payload["schema_version"] == "av3-trust-v1"
    assert xai_payload["schema_version"] == "av3-xai-v1"


def test_trust_packet_includes_calibrated_review_reasons_for_failed_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-failed"
    _seed_failed_review_run(run_dir)

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))
    packet = build_trust_intelligence_packet(run_dir, summary=summary)
    overall = packet["trust_signals"]["overall"]
    validation_signal = packet["trust_signals"]["validation_signal"]

    assert overall["status"] == "low"
    assert overall["requires_human_review"] is True
    assert "quality_gate_hard_blocked" in overall["review_reasons"]
    assert "run_status=failed" in overall["review_reasons"]
    assert validation_signal["quality_score_normalized"] == 0.42
    assert validation_signal["gate_pass_rate"] == 0.0
    assert packet["operator_next"]["review_reasons"] == overall["review_reasons"]


def test_trust_summary_cli_outputs_json_and_text(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-cli"
    _seed_completed_run(run_dir)

    autonomous_mode.cli(["trust-summary", "--run-dir", str(run_dir)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["trust_signals"]["overall"]["status"] == "high"
    assert payload["latest_quality"]["status"] == "passed"

    autonomous_mode.cli(["trust-summary", "--run-dir", str(run_dir), "--format", "text"])
    text_out = capsys.readouterr().out
    assert "# Autonomous Trust Intelligence" in text_out
    assert "trust_status: high" in text_out
    assert "latest_quality_status: passed" in text_out
