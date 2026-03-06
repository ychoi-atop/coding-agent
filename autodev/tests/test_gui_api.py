from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autodev.gui_api import (
    GuiApiError,
    _reset_process_manager_for_tests,
    build_resume_command,
    build_start_command,
    get_process_detail,
    get_process_history,
    get_run_detail,
    list_processes,
    list_runs,
    read_artifact,
    trigger_resume,
    trigger_retry,
    trigger_start,
    trigger_stop,
    validate_resume_target,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_process_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "gui-process-state.json"
    monkeypatch.setenv("AUTODEV_GUI_PROCESS_STATE_FILE", str(state_file))
    _reset_process_manager_for_tests()


class _FakeProcess:
    _next_pid = 1000

    def __init__(self, *, poll_result: int | None = None, terminate_timeout: bool = False, wait_returncode: int = 0) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self._poll_result = poll_result
        self._terminate_timeout = terminate_timeout
        self._wait_returncode = wait_returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        if self.killed:
            return -9
        if self.terminated and not self._terminate_timeout:
            return self._wait_returncode
        return self._poll_result

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return -9
        if self.terminated and self._terminate_timeout:
            raise subprocess.TimeoutExpired(cmd="autodev", timeout=timeout or 0)
        return self._wait_returncode


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


def test_trigger_start_execute_tracks_process_and_stop_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProcess(poll_result=None, terminate_timeout=False, wait_returncode=0)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    payload = {
        "prd": "examples/PRD.md",
        "out": "./generated_runs",
        "profile": "enterprise",
    }
    started = trigger_start(payload, execute=True)
    assert started["spawned"] is True
    assert started["process"]["state"] == "running"
    process_id = started["process"]["process_id"]

    stopped = trigger_stop({"process_id": process_id}, graceful_timeout_sec=0.1)
    assert stopped["ok"] is True
    assert stopped["stopped"] is True
    assert stopped["process"]["state"] == "terminated"
    assert stopped["process"]["stop_reason"] == "graceful"


def test_trigger_stop_forced_kill_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProcess(poll_result=None, terminate_timeout=True, wait_returncode=0)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    payload = {
        "prd": "examples/PRD.md",
        "out": "./generated_runs",
        "profile": "enterprise",
    }
    started = trigger_start(payload, execute=True)
    process_id = started["process"]["process_id"]

    stopped = trigger_stop({"process_id": process_id}, graceful_timeout_sec=0.1)
    assert stopped["process"]["state"] == "killed"
    assert stopped["process"]["stop_reason"] == "forced"


def test_trigger_retry_preserves_chain_and_linkage(monkeypatch: pytest.MonkeyPatch) -> None:
    fakes = [_FakeProcess(poll_result=None), _FakeProcess(poll_result=None)]

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fakes.pop(0)

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    payload = {
        "prd": "examples/PRD.md",
        "out": "./generated_runs/run-a",
        "profile": "enterprise",
    }
    first = trigger_start(payload, execute=True)
    first_process = first["process"]

    retried = trigger_retry({"process_id": first_process["process_id"]}, execute=True)
    assert retried["spawned"] is True
    assert retried["retry_of"] == first_process["process_id"]
    assert retried["process"]["retry_root"] == first_process["retry_root"]
    assert retried["process"]["retry_attempt"] == 2
    assert retried["run_link"]["out"] == "./generated_runs/run-a"


def test_trigger_retry_unknown_process_raises_not_found() -> None:
    with pytest.raises(FileNotFoundError, match="Unknown process_id"):
        trigger_retry({"process_id": "proc-missing"}, execute=False)


def test_process_state_persists_and_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProcess(poll_result=None)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    started = trigger_start(
        {
            "prd": "examples/PRD.md",
            "out": "./generated_runs/run-persist",
            "profile": "enterprise",
        },
        execute=True,
    )
    process_id = started["process"]["process_id"]

    # Simulate server restart: manager should reload from persisted state file.
    _reset_process_manager_for_tests()

    reloaded = get_process_detail(process_id)
    assert reloaded["process_id"] == process_id
    assert reloaded["run_link"]["run_id"] == "run-persist"
    assert reloaded["command"][0] == "autodev"


def test_list_detail_history_read_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProcess(poll_result=None)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    started = trigger_start(
        {
            "prd": "examples/PRD.md",
            "out": "./generated_runs/run-read",
            "profile": "enterprise",
        },
        execute=True,
    )
    process_id = started["process"]["process_id"]

    listed = list_processes(limit=10)
    assert listed["count"] == 1
    assert listed["processes"][0]["process_id"] == process_id

    detail = get_process_detail(process_id)
    assert detail["run_link"]["run_id"] == "run-read"

    history = get_process_history(process_id)
    assert history["process_id"] == process_id
    assert [row["state"] for row in history["history"]][:2] == ["spawned", "running"]


def test_trigger_retry_by_run_id_preserves_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    fakes = [_FakeProcess(poll_result=None), _FakeProcess(poll_result=None)]

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fakes.pop(0)

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    first = trigger_start(
        {
            "prd": "examples/PRD.md",
            "out": "./generated_runs/run-by-id",
            "profile": "enterprise",
        },
        execute=True,
    )

    retried = trigger_retry({"run_id": "run-by-id"}, execute=True)
    assert retried["spawned"] is True
    assert retried["retry_of"] == first["process"]["process_id"]
    assert retried["process"]["retry_root"] == first["process"]["retry_root"]
    assert retried["process"]["retry_attempt"] == 2


def test_trigger_retry_requires_process_or_run_id() -> None:
    with pytest.raises(GuiApiError, match="'process_id' or 'run_id' is required"):
        trigger_retry({}, execute=False)
