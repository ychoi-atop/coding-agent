from __future__ import annotations

import json
from pathlib import Path

import pytest

from autodev.gui_api import (
    GuiApiError,
    build_resume_command,
    build_start_command,
    get_run_detail,
    list_runs,
    read_artifact,
    trigger_resume,
    trigger_start,
    validate_resume_target,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_list_runs_and_get_run_detail(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run1 = out_root / "run-a"
    run2 = out_root / "run-b"

    _write_json(
        run1 / ".autodev" / "run_metadata.json",
        {
            "run_id": "rid-a",
            "request_id": "req-a",
            "requested_profile": "enterprise",
            "result_ok": True,
            "llm": {"model": "anthropic/claude-sonnet-4-6"},
        },
    )
    _write_json(run1 / ".autodev" / "checkpoint.json", {"status": "completed"})

    _write_json(
        run2 / ".autodev" / "run_metadata.json",
        {
            "run_id": "rid-b",
            "request_id": "req-b",
            "requested_profile": "fast",
            "result_ok": False,
            "llm": {"model": "openai/gpt-4.1"},
        },
    )
    _write_json(run2 / ".autodev" / "checkpoint.json", {"status": "failed"})

    rows = list_runs(str(out_root), limit=10)
    assert len(rows) == 2
    assert {row["run_id"] for row in rows} == {"rid-a", "rid-b"}

    by_id = {row["run_id"]: row for row in rows}
    assert by_id["rid-a"]["status"] == "ok"
    assert by_id["rid-b"]["status"] == "failed"
    assert by_id["rid-a"]["artifact_errors"] == []
    assert by_id["rid-a"]["artifact_schema_versions"]["run_metadata"]["effective_version"] == "legacy-v0"
    assert by_id["rid-a"]["artifact_schema_warnings"] == []

    detail = get_run_detail(str(out_root), "rid-a")
    assert detail["run_name"] == "run-a"
    assert detail["status"] == "ok"
    assert detail["run_metadata"]["request_id"] == "req-a"
    assert detail["artifact_errors"] == []
    assert detail["artifact_schema_versions"]["run_trace"]["effective_version"] == "legacy-v0"
    assert detail["artifact_schema_warnings"] == []


def test_read_artifact_json_and_markdown(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-1"

    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-1"})
    _write_json(run / ".autodev" / "plan.json", {"tasks": [{"id": "api"}]})
    (run / ".autodev" / "REPORT.md").parent.mkdir(parents=True, exist_ok=True)
    (run / ".autodev" / "REPORT.md").write_text("# Report", encoding="utf-8")

    plan = read_artifact(str(out_root), "rid-1", "plan.json")
    assert plan["content_type"] == "application/json"
    assert plan["content"]["tasks"][0]["id"] == "api"
    assert "error" not in plan

    report = read_artifact(str(out_root), "run-1", ".autodev/REPORT.md")
    assert report["content_type"] == "text/markdown"
    assert report["content"] == "# Report"


def test_read_artifact_returns_structured_json_error_for_malformed_json(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-err"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-err"})
    bad = run / ".autodev" / "plan.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"tasks": [', encoding="utf-8")

    res = read_artifact(str(out_root), "rid-err", "plan.json")
    assert res["content_type"] == "application/json"
    assert res["content"] is None
    assert res["error"]["kind"] == "artifact_json_error"
    assert res["error"]["code"] == "artifact_json_malformed"
    assert res["error"]["path"] == ".autodev/plan.json"


def test_read_artifact_marks_truncated_json_error_code(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-trunc"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-trunc"})
    payload = {"tasks": [{"id": "x", "note": "a" * 128}]}
    _write_json(run / ".autodev" / "plan.json", payload)

    res = read_artifact(str(out_root), "rid-trunc", "plan.json", max_bytes=20)
    assert res["truncated"] is True
    assert res["content"] is None
    assert res["error"]["code"] == "artifact_json_truncated"


def test_list_runs_and_detail_include_json_parse_errors(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-bad"
    (run / ".autodev").mkdir(parents=True, exist_ok=True)
    (run / ".autodev" / "run_metadata.json").write_text('{"run_id": "broken"', encoding="utf-8")
    _write_json(run / ".autodev" / "checkpoint.json", {"status": "running"})

    rows = list_runs(str(out_root), limit=5)
    assert len(rows) == 1
    assert rows[0]["status"] == "running"
    assert rows[0]["artifact_errors"][0]["code"] == "artifact_json_malformed"
    assert rows[0]["artifact_errors"][0]["path"].endswith("run_metadata.json")

    detail = get_run_detail(str(out_root), "run-bad")
    assert detail["status"] == "running"
    assert detail["run_metadata"] is None
    assert detail["artifact_errors"][0]["code"] == "artifact_json_malformed"


def test_unknown_artifact_schema_version_adds_warning_with_fallback(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-unknown"
    _write_json(
        run / ".autodev" / "run_metadata.json",
        {
            "run_id": "rid-unknown",
            "schema_version": "future-v99",
        },
    )
    _write_json(run / ".autodev" / "checkpoint.json", {"status": "running"})

    rows = list_runs(str(out_root), limit=10)
    assert rows[0]["artifact_schema_versions"]["run_metadata"]["declared_version"] == "future-v99"
    assert rows[0]["artifact_schema_versions"]["run_metadata"]["effective_version"] == "legacy-v0"
    assert rows[0]["artifact_schema_versions"]["run_metadata"]["known_version"] is False
    assert rows[0]["artifact_schema_warnings"][0]["code"] == "unknown_schema_version"

    detail = get_run_detail(str(out_root), "rid-unknown")
    warning = detail["artifact_schema_warnings"][0]
    assert warning["artifact"] == "run_metadata"
    assert warning["fallback_version"] == "legacy-v0"


def test_read_artifact_includes_schema_marker_and_unknown_warning(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-artifact"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-artifact"})
    _write_json(
        run / ".autodev" / "run_trace.json",
        {
            "schema_version": "future-v2",
            "events": [],
        },
    )

    res = read_artifact(str(out_root), "rid-artifact", "run_trace.json")
    assert res["artifact_schema"]["artifact"] == "run_trace"
    assert res["artifact_schema"]["known_version"] is False
    assert res["artifact_schema"]["effective_version"] == "legacy-v0"
    assert res["warning"]["code"] == "unknown_schema_version"


def test_read_artifact_blocks_traversal(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-1"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-1"})

    with pytest.raises(GuiApiError):
        read_artifact(str(out_root), "rid-1", "../../etc/passwd")


def test_list_runs_normalizes_checkpoint_status_alias(tmp_path: Path) -> None:
    out_root = tmp_path / "generated_runs"
    run = out_root / "run-c"

    _write_json(run / ".autodev" / "checkpoint.json", {"status": "completed"})

    rows = list_runs(str(out_root), limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


def test_build_start_and_resume_command() -> None:
    payload = {
        "prd": "examples/PRD.md",
        "out": "./generated_runs",
        "profile": "enterprise",
        "model": "anthropic/claude-sonnet-4-6",
        "interactive": True,
        "config": "config.yaml",
    }

    start_cmd = build_start_command(payload)
    assert start_cmd[:3] == ["autodev", "--prd", "examples/PRD.md"]
    assert "--interactive" in start_cmd
    assert "--resume" not in start_cmd

    resume_cmd = build_resume_command(payload)
    assert resume_cmd[-1] == "--resume"


def test_build_command_rejects_unsafe_profile() -> None:
    with pytest.raises(GuiApiError):
        build_start_command(
            {
                "prd": "examples/PRD.md",
                "out": "./generated_runs",
                "profile": "enterprise;rm -rf /",
            }
        )


def test_trigger_wrappers_dry_run_shape() -> None:
    payload = {
        "prd": "examples/PRD.md",
        "out": "./generated_runs",
        "profile": "enterprise",
    }
    start = trigger_start(payload, execute=False)
    assert start["ok"] is True
    assert start["spawned"] is False
    assert start["audit_event"]["action"] == "start"

    resume = trigger_resume(payload, execute=False)
    assert resume["ok"] is True
    assert resume["spawned"] is False
    assert resume["audit_event"]["action"] == "resume"
    assert "--resume" in resume["command"]


def test_validate_resume_target_success(tmp_path: Path) -> None:
    run = tmp_path / "run-1"
    _write_json(
        run / ".autodev" / "run_metadata.json",
        {
            "run_id": "rid-1",
            "requested_profile": "enterprise",
        },
    )
    _write_json(
        run / ".autodev" / "checkpoint.json",
        {
            "status": "running",
            "completed_task_ids": ["bootstrap"],
        },
    )

    result = validate_resume_target(str(run))
    assert result["run_id"] == "rid-1"
    assert result["status"] == "running"
    assert result["completed_task_count"] == 1


def test_validate_resume_target_rejects_missing_markers(tmp_path: Path) -> None:
    run = tmp_path / "run-2"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-2"})
    _write_json(run / ".autodev" / "checkpoint.json", {"status": "running"})

    with pytest.raises(GuiApiError, match="missing resumable markers"):
        validate_resume_target(str(run))


def test_validate_resume_target_rejects_terminal_run(tmp_path: Path) -> None:
    run = tmp_path / "run-3"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "rid-3", "result_ok": True})
    _write_json(
        run / ".autodev" / "checkpoint.json",
        {"status": "completed", "completed_task_ids": ["task-a"]},
    )

    with pytest.raises(GuiApiError, match="finalized"):
        validate_resume_target(str(run))
