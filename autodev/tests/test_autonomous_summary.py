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

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))

    assert summary["status"] == "failed"
    assert summary["gate_counts"] == {"pass": 1, "fail": 2, "total": 3}
    assert summary["dominant_fail_codes"][0] == {"code": "tests.min_pass_rate_not_met", "count": 2}
    assert summary["dominant_fail_codes"][1] == {"code": "security.max_high_findings_exceeded", "count": 1}
    assert summary["latest_strategy"]["name"] == "security-focused"
    assert summary["latest_strategy_source"] == "strategy_trace"
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
    assert len(summary["warnings"]) == 2
    assert any("gate_results: missing" in item for item in summary["warnings"])
    assert any("strategy_trace: missing" in item for item in summary["warnings"])


def test_autonomous_summary_cli_outputs_json_and_text(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-cli"
    artifacts = run_dir / ".autodev"

    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": False,
            "run_id": "run-cli",
            "latest_strategy": {"name": "mixed"},
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

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir)])
    json_out = capsys.readouterr().out
    payload = json.loads(json_out)
    assert payload["status"] == "failed"
    assert payload["gate_counts"]["fail"] == 1

    autonomous_mode.cli(["summary", "--run-dir", str(run_dir), "--format", "text"])
    text_out = capsys.readouterr().out
    assert "# Autonomous Run Summary" in text_out
    assert "status: failed" in text_out
    assert "dominant_fail_codes: tests.min_pass_rate_not_met(1)" in text_out
