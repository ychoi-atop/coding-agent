from __future__ import annotations

from autodev.gui_mvp_dto import normalize_run_trace, normalize_tasks, normalize_validation


def test_normalize_run_trace_supports_phase_timeline_alias() -> None:
    dto = normalize_run_trace(
        {
            "model": "m1",
            "phase_timeline": [{"name": "implementation", "duration_ms": 1234}],
            "events": [{"event": "run.start", "timestamp": "2026-03-05T01:00:00Z"}],
        }
    )
    assert dto["model"] == "m1"
    assert dto["phase_timeline"][0]["phase"] == "implementation"
    assert dto["started_at"] == "2026-03-05T01:00:00Z"


def test_normalize_tasks_aggregates_attempt_rows() -> None:
    rows = normalize_tasks(
        {
            "tasks": [
                {"task_id": "task-1", "attempt": 1, "status": "failed", "hard_failures": 1, "duration_ms": 100},
                {"task_id": "task-1", "attempt": 2, "status": "passed", "hard_failures": 0, "duration_ms": 50},
            ]
        }
    )
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["status"] == "passed"
    assert rows[0]["attempts"] >= 2
    assert rows[0]["hard_failures"] == 1
    assert rows[0]["duration_ms"] == 150


def test_normalize_validation_prefers_final_artifact_and_builds_summary() -> None:
    dto = normalize_validation(
        {
            "validation": [
                {"name": "ruff", "ok": False, "status": "failed", "duration_ms": 12},
                {"name": "pytest", "ok": True, "status": "passed", "duration_ms": 45},
            ]
        },
        {
            "final": {
                "validations": [
                    {"name": "ruff", "ok": True, "status": "passed"},
                ]
            }
        },
    )

    assert dto["summary"]["total"] == 2
    assert dto["summary"]["failed"] == 1
    assert dto["summary"]["passed"] == 1
    assert dto["summary"]["by_validator"]["ruff"] == 1
    assert {row["name"] for row in dto["validator_cards"]} == {"ruff", "pytest"}
