from __future__ import annotations

import json
from pathlib import Path

from autodev.gui_mvp_dto import (
    normalize_run_comparison,
    normalize_run_comparison_summary,
    normalize_run_trace,
    normalize_tasks,
    normalize_validation,
)


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

    run_start = dto["timeline_events"][0]
    assert run_start["event_type"] == "run.start"
    assert run_start["event_category"] == "run"

    synthesized_phase = [row for row in dto["timeline_events"] if row["event_type"] == "phase.end"][0]
    assert synthesized_phase["phase"] == "implementation"
    assert synthesized_phase["source"] == "phase_timeline"


def test_normalize_run_trace_normalizes_event_taxonomy_aliases() -> None:
    dto = normalize_run_trace(
        {
            "trace_events": [
                {"type": "run_start", "timestamp": "2026-03-06T01:00:00Z"},
                {"type": "phase.completed", "phase": "planning", "elapsed_ms": 500, "status": "ok"},
                {"type": "run.end", "timestamp": "2026-03-06T01:00:02Z"},
            ]
        }
    )

    assert dto["started_at"] == "2026-03-06T01:00:00Z"
    assert dto["completed_at"] == "2026-03-06T01:00:02Z"

    types = [row["event_type"] for row in dto["timeline_events"]]
    assert "run.start" in types
    assert "phase.end" in types
    assert "run.completed" in types


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


def test_normalize_validation_handles_outputs_and_status_aliases() -> None:
    dto = normalize_validation(
        {
            "validation": [
                {
                    "name": "pytest",
                    "status": "failed",
                    "ok": False,
                    "stderr": "AssertionError",
                    "stdout": "collected 4 items",
                },
                {
                    "name": "lint",
                    "status": "ok",
                },
            ]
        },
        {},
    )

    by_name = {row["name"]: row for row in dto["validator_cards"]}
    assert by_name["pytest"]["stderr"] == "AssertionError"
    assert by_name["pytest"]["stdout"] == "collected 4 items"
    assert by_name["pytest"]["scope"] == "final"
    assert by_name["pytest"]["artifact_path"] == ".autodev/task_final_last_validation.json"
    assert by_name["lint"]["status"] == "passed"


def test_normalize_validation_appends_per_task_rows_for_triage_context() -> None:
    dto = normalize_validation(
        {
            "validation": [
                {"name": "pytest", "status": "failed", "ok": False},
            ]
        },
        {
            "tasks": [
                {
                    "task_id": "task-auth",
                    "last_validation": [
                        {"name": "pytest", "status": "failed", "ok": False, "stderr": "test_login failed"},
                        {"name": "ruff", "status": "passed", "ok": True},
                    ],
                }
            ]
        },
    )

    cards = dto["validator_cards"]
    assert len(cards) == 3

    final_row = cards[0]
    assert final_row["name"] == "pytest"
    assert final_row["scope"] == "final"
    assert final_row["task_id"] == ""

    per_task = [row for row in cards if row["scope"] == "task" and row["name"] == "pytest"][0]
    assert per_task["task_id"] == "task-auth"
    assert per_task["artifact_path"] == ".autodev/task_task-auth_last_validation.json"
    assert per_task["stderr"] == "test_login failed"


def test_normalize_run_comparison_summary_uses_explicit_defaults() -> None:
    dto = normalize_run_comparison_summary({"run_id": "run-empty"})

    assert dto["run_id"] == "run-empty"
    assert dto["status"] == "unknown"
    assert dto["totals"] == {
        "total_task_attempts": 0,
        "hard_failures": 0,
        "soft_failures": 0,
        "task_count": 0,
        "blocker_count": 0,
    }
    assert dto["validation"] == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "soft_fail": 0,
        "skipped": 0,
        "blocking_failed": 0,
    }
    assert dto["timeline"] == {"phase_count": 0, "total_duration_ms": 0}
    assert dto["blockers"] == []


def test_normalize_run_comparison_summary_handles_mixed_schema_versions() -> None:
    dto = normalize_run_comparison_summary(
        {
            "run_id": "run-legacy",
            "status": "completed",
            "summary": {
                "project": {"type": "python_cli"},
                "totals": {"attempts": 9, "hard": 2, "soft": 1},
                "profile": {"name": "minimal"},
            },
            "metadata": {"model": "m1"},
            "phase_timeline": [{"phase": "planning", "duration_ms": 250}],
            "validation": {
                "results": [
                    {"name": "ruff", "status": "ok"},
                    {"name": "pytest", "status": "error"},
                    {"name": "typing", "status": "warning"},
                    {"name": "dep", "status": "skipped"},
                ]
            },
            "quality_index": {"final": {"status": "completed"}},
        }
    )

    assert dto["status"] == "ok"
    assert dto["totals"]["total_task_attempts"] == 9
    assert dto["totals"]["hard_failures"] == 2
    assert dto["totals"]["soft_failures"] == 1
    assert dto["validation"] == {
        "total": 4,
        "passed": 1,
        "failed": 1,
        "soft_fail": 1,
        "skipped": 1,
        "blocking_failed": 1,
    }
    assert dto["timeline"]["phase_count"] == 1
    assert dto["timeline"]["total_duration_ms"] == 250


def test_normalize_run_comparison_highlights_key_differences() -> None:
    payload = normalize_run_comparison(
        {
            "run_id": "run-a",
            "status": "failed",
            "summary": {
                "project": {"type": "python_cli"},
                "totals": {"total_task_attempts": 4, "hard_failures": 1, "soft_failures": 1},
                "profile": {"name": "minimal"},
            },
            "metadata": {"model": "m1"},
            "blockers": ["final_validation"],
            "validation_normalized": {
                "summary": {"total": 2, "passed": 1, "failed": 1, "soft_fail": 0, "skipped": 0},
                "validator_cards": [
                    {"name": "ruff", "status": "failed", "ok": False},
                    {"name": "pytest", "status": "passed", "ok": True},
                ],
            },
        },
        {
            "run_id": "run-b",
            "status": "ok",
            "summary": {
                "project": {"type": "python_cli"},
                "totals": {"total_task_attempts": 3, "hard_failures": 0, "soft_failures": 1},
                "profile": {"name": "enterprise"},
            },
            "metadata": {"model": "m2"},
            "blockers": ["dependency_blocked"],
            "validation_normalized": {
                "summary": {"total": 3, "passed": 2, "failed": 0, "soft_fail": 1, "skipped": 0},
                "validator_cards": [
                    {"name": "ruff", "status": "passed", "ok": True},
                    {"name": "pytest", "status": "passed", "ok": True},
                    {"name": "mypy", "status": "soft_fail", "ok": False},
                ],
            },
        },
    )

    assert payload["schema_version"] == "shw-012-v1"
    assert payload["diff"]["status_changed"] is True
    assert payload["diff"]["blockers"]["only_left"] == ["final_validation"]
    assert payload["diff"]["blockers"]["only_right"] == ["dependency_blocked"]

    changed = payload["diff"]["validation"]["changed"]
    assert changed == [{"name": "ruff", "left": "failed", "right": "passed"}]
    assert payload["diff"]["validation"]["only_right"] == [{"name": "mypy", "status": "soft_fail"}]


def _load_compat_fixture(filename: str) -> dict[str, object]:
    root = Path(__file__).resolve().parent / "fixtures" / "gui_compat"
    return json.loads((root / filename).read_text(encoding="utf-8"))


def test_run_trace_compatibility_fixtures_snapshot() -> None:
    fixture = _load_compat_fixture("run_trace_variants.json")
    cases = fixture.get("cases")
    assert isinstance(cases, list)

    for case in cases:
        assert isinstance(case, dict)
        payload = normalize_run_trace(case.get("input"))
        assert payload == case.get("expected")


def test_validation_compatibility_fixtures_snapshot() -> None:
    fixture = _load_compat_fixture("validation_variants.json")
    cases = fixture.get("cases")
    assert isinstance(cases, list)

    for case in cases:
        assert isinstance(case, dict)
        payload = normalize_validation(case.get("final_validation"), case.get("quality_index"))
        assert payload == case.get("expected")
