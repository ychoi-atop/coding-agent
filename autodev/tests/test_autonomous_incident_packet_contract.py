from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_mode as autonomous_mode  # noqa: E402


def _load_fixture() -> dict[str, Any]:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "incident_packet" / "minimal_contract_v2.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _get_path(payload: dict[str, Any], path: str) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _minimal_projection(packet: dict[str, Any]) -> dict[str, Any]:
    run_summary = packet.get("run_summary") if isinstance(packet.get("run_summary"), dict) else {}
    failure_codes = packet.get("failure_codes") if isinstance(packet.get("failure_codes"), dict) else {}
    routing = packet.get("incident_routing") if isinstance(packet.get("incident_routing"), dict) else {}
    routing_primary = routing.get("primary") if isinstance(routing.get("primary"), dict) else {}
    guidance = packet.get("operator_guidance") if isinstance(packet.get("operator_guidance"), dict) else {}
    top_actions = guidance.get("top_actions") if isinstance(guidance.get("top_actions"), list) else []
    top_action = top_actions[0] if top_actions and isinstance(top_actions[0], dict) else {}
    reproduction = packet.get("reproduction") if isinstance(packet.get("reproduction"), dict) else {}
    artifact_paths = reproduction.get("artifact_paths") if isinstance(reproduction.get("artifact_paths"), dict) else {}

    return {
        "schema_version": packet.get("schema_version"),
        "status": packet.get("status"),
        "run_summary": {
            "run_id": run_summary.get("run_id"),
            "request_id": run_summary.get("request_id"),
            "failure_reason": run_summary.get("failure_reason"),
            "completed_at": run_summary.get("completed_at"),
        },
        "failure_codes": {
            "typed_codes": failure_codes.get("typed_codes"),
            "root_cause_codes": failure_codes.get("root_cause_codes"),
        },
        "incident_routing": {
            "primary": {
                "owner_team": routing_primary.get("owner_team"),
                "severity": routing_primary.get("severity"),
                "target_sla": routing_primary.get("target_sla"),
                "escalation_class": routing_primary.get("escalation_class"),
            }
        },
        "operator_guidance": {
            "playbook": guidance.get("playbook"),
            "top_action": {
                "code": top_action.get("code"),
                "action": top_action.get("action"),
                "playbook_url": top_action.get("playbook_url"),
            },
        },
        "reproduction": {
            "run_dir": reproduction.get("run_dir"),
            "artifact_paths": {
                "report_json": artifact_paths.get("report_json"),
                "incident_packet": artifact_paths.get("incident_packet"),
            },
        },
    }


def test_incident_packet_minimal_field_contract_v2_snapshot(monkeypatch) -> None:
    fixture = _load_fixture()

    monkeypatch.setattr(autonomous_mode, "_utc_now", lambda: "2026-03-09T02:24:00Z")

    state = {
        "run_id": "run-contract-v2",
        "request_id": "req-contract-v2",
        "run_out": "/tmp/run-contract-v2",
        "profile": "minimal",
        "failure_reason": "autonomous_guard_stop",
        "attempts": [
            {
                "iteration": 1,
                "ok": False,
                "gate_results": {
                    "passed": False,
                    "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
                },
            }
        ],
        "preflight": {"status": "passed", "reason_codes": []},
    }

    report, _ = autonomous_mode._render_report(state, ok=False, last_validation=[])
    packet = autonomous_mode._build_autonomous_incident_packet(state=state, report=report, ok=False)

    assert packet is not None

    required_paths = fixture.get("required_paths")
    assert isinstance(required_paths, list)
    for path in required_paths:
        assert isinstance(path, str)
        assert _get_path(packet, path) is not None, f"missing required path: {path}"

    assert _minimal_projection(packet) == fixture.get("expected_projection")
