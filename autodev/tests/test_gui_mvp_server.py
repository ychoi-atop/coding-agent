from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest

from autodev.gui_mvp_server import (
    GuiConfig,
    GuiRequestHandler,
    _list_runs,
    _resolve_request_role,
    _run_compare,
    _run_detail,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_list_runs_empty(tmp_path):
    runs = _list_runs(tmp_path / "missing")
    assert runs == []


def test_list_runs_and_detail(tmp_path):
    run_dir = tmp_path / "run-001"
    _write_json(
        run_dir / ".autodev" / "task_quality_index.json",
        {
            "project": {"type": "python_cli"},
            "resolved_quality_profile": {"name": "minimal"},
            "tasks": [{"task_id": "task-1", "status": "passed", "attempts": 1}],
            "unresolved_blockers": ["final_validation"],
            "final": {
                "status": "failed",
                "validations": [{"name": "ruff", "ok": False, "status": "failed", "returncode": 1}],
            },
            "totals": {"total_task_attempts": 1},
        },
    )
    _write_json(
        run_dir / ".autodev" / "task_final_last_validation.json",
        {"validation": [{"name": "ruff", "ok": False}]},
    )
    _write_json(
        run_dir / ".autodev" / "run_trace.json",
        {
            "llm": {"model": "anthropic/claude-sonnet-4-6"},
            "profile": "minimal",
            "events": [
                {"event_type": "run.start", "timestamp": "2026-03-05T00:00:00Z"},
                {"event_type": "run.completed", "timestamp": "2026-03-05T00:01:00Z"},
            ],
            "phases": [{"phase": "planning", "duration_ms": 1000}],
        },
    )

    rows = _list_runs(tmp_path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-001"
    assert rows[0]["status"] == "failed"
    assert rows[0]["project_type"] == "python_cli"
    assert rows[0]["model"] == "anthropic/claude-sonnet-4-6"
    assert rows[0]["artifact_errors"] == []
    assert rows[0]["artifact_schema_versions"]["task_quality_index"]["effective_version"] == "legacy-v0"
    assert rows[0]["artifact_schema_warnings"] == []

    detail = _run_detail(run_dir)
    assert detail["status"] == "failed"
    assert detail["tasks"][0]["task_id"] == "task-1"
    assert detail["blockers"] == ["final_validation"]
    assert detail["phase_timeline"][0]["phase"] == "planning"
    assert detail["validation"]["validation"][0]["name"] == "ruff"
    assert detail["metadata"]["profile"] == "minimal"
    assert detail["metadata"]["model"] == "anthropic/claude-sonnet-4-6"
    assert detail["metadata"]["started_at"] == "2026-03-05T00:00:00Z"
    assert detail["metadata"]["completed_at"] == "2026-03-05T00:01:00Z"
    assert detail["validation_normalized"]["summary"]["total"] == 1
    assert detail["validation_normalized"]["summary"]["failed"] == 1
    assert detail["artifact_errors"] == []
    assert detail["artifact_schema_versions"]["run_trace"]["effective_version"] == "legacy-v0"
    assert detail["artifact_schema_warnings"] == []


def test_list_runs_normalizes_quality_status_alias(tmp_path):
    run_dir = tmp_path / "run-alias"
    _write_json(
        run_dir / ".autodev" / "task_quality_index.json",
        {
            "final": {"status": "completed"},
        },
    )

    rows = _list_runs(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


def test_run_detail_normalizes_run_trace_event_only_timeline(tmp_path):
    run_dir = tmp_path / "run-evt"
    _write_json(
        run_dir / ".autodev" / "task_quality_index.json",
        {
            "resolved_quality_profile": {"name": "enterprise"},
            "tasks": [
                {"task_id": "task-a", "attempt": 1, "status": "failed", "hard_failures": 1, "soft_failures": 0},
                {"task_id": "task-a", "attempt": 2, "status": "passed", "hard_failures": 0, "soft_failures": 0},
            ],
            "final": {"status": "ok"},
        },
    )
    _write_json(run_dir / ".autodev" / "task_final_last_validation.json", {"results": [{"validator": "pytest", "ok": True}]})
    _write_json(
        run_dir / ".autodev" / "run_trace.json",
        {
            "config": {"llm": {"model": "openai/gpt-5.3"}},
            "events": [
                {"event_type": "phase.start", "phase": "planning", "elapsed_ms": 100},
                {"event_type": "phase.end", "phase": "planning", "elapsed_ms": 900, "status": "completed"},
            ],
        },
    )

    detail = _run_detail(run_dir)
    assert detail["metadata"]["model"] == "openai/gpt-5.3"
    assert detail["metadata"]["profile"] == "enterprise"
    assert detail["phase_timeline"][0]["phase"] == "planning"
    assert detail["phase_timeline"][0]["duration_ms"] == 800
    assert detail["tasks"][0]["task_id"] == "task-a"
    assert detail["tasks"][0]["attempts"] >= 2
    assert detail["tasks"][0]["status"] == "passed"
    assert detail["validation_normalized"]["summary"]["total"] == 1
    assert detail["validation_normalized"]["summary"]["passed"] == 1


def test_malformed_json_returns_artifact_errors_without_crash(tmp_path):
    run_dir = tmp_path / "run-bad-json"
    (run_dir / ".autodev").mkdir(parents=True, exist_ok=True)
    (run_dir / ".autodev" / "task_quality_index.json").write_text('{"final": ', encoding="utf-8")
    (run_dir / ".autodev" / "run_trace.json").write_text('{"events": [', encoding="utf-8")

    rows = _list_runs(tmp_path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-bad-json"
    assert rows[0]["status"] == "unknown"
    assert len(rows[0]["artifact_errors"]) == 2
    assert rows[0]["artifact_errors"][0]["code"] == "artifact_json_malformed"

    detail = _run_detail(run_dir)
    assert detail["status"] == "unknown"
    assert detail["quality_index"] == {}
    assert detail["validation"] == {}
    assert len(detail["artifact_errors"]) == 2
    assert {err["path"].split("/")[-1] for err in detail["artifact_errors"]} == {
        "task_quality_index.json",
        "run_trace.json",
    }


def test_unknown_artifact_schema_version_includes_warning_and_fallback(tmp_path):
    run_dir = tmp_path / "run-schema-unknown"
    _write_json(
        run_dir / ".autodev" / "task_quality_index.json",
        {
            "schema_version": "future-v3",
            "final": {"status": "running"},
        },
    )
    _write_json(run_dir / ".autodev" / "task_final_last_validation.json", {"validation": []})

    rows = _list_runs(tmp_path)
    assert rows[0]["artifact_schema_versions"]["task_quality_index"]["known_version"] is False
    assert rows[0]["artifact_schema_versions"]["task_quality_index"]["effective_version"] == "legacy-v0"
    assert rows[0]["artifact_schema_warnings"][0]["code"] == "unknown_schema_version"

    detail = _run_detail(run_dir)
    warning = detail["artifact_schema_warnings"][0]
    assert warning["artifact"] == "task_quality_index"
    assert warning["fallback_version"] == "legacy-v0"


def test_run_compare_returns_normalized_summary_for_two_runs(tmp_path):
    run_a = tmp_path / "run-a"
    _write_json(
        run_a / ".autodev" / "task_quality_index.json",
        {
            "project": {"type": "python_cli"},
            "resolved_quality_profile": {"name": "minimal"},
            "totals": {"total_task_attempts": 3, "hard_failures": 1, "soft_failures": 0},
            "unresolved_blockers": ["final_validation"],
            "final": {"status": "failed"},
        },
    )
    _write_json(
        run_a / ".autodev" / "task_final_last_validation.json",
        {"validation": [{"name": "ruff", "status": "failed", "ok": False}]},
    )
    _write_json(
        run_a / ".autodev" / "run_trace.json",
        {"llm": {"model": "m-a"}, "phase_timeline": [{"phase": "planning", "duration_ms": 100}]},
    )

    run_b = tmp_path / "run-b"
    _write_json(
        run_b / ".autodev" / "task_quality_index.json",
        {
            "project": {"type": "python_cli"},
            "resolved_quality_profile": {"name": "enterprise"},
            "totals": {"attempts": 5, "hard": 0, "soft": 1},
            "final": {"status": "completed"},
        },
    )
    _write_json(
        run_b / ".autodev" / "task_final_last_validation.json",
        {
            "results": [
                {"name": "ruff", "status": "ok"},
                {"name": "pytest", "status": "ok"},
            ]
        },
    )
    _write_json(run_b / ".autodev" / "run_trace.json", {"model": "m-b"})

    payload, status = _run_compare(tmp_path, "run-a", "run-b")
    assert status == 200
    assert payload["left"]["status"] == "failed"
    assert payload["right"]["status"] == "ok"
    assert payload["left"]["totals"]["blocker_count"] == 1
    assert payload["right"]["totals"]["total_task_attempts"] == 5
    assert payload["right"]["validation"]["passed"] == 2
    assert payload["delta"]["validation_passed"] == 2
    assert payload["delta"]["hard_failures"] == -1


def test_compare_endpoint_supports_legacy_query_aliases(gui_server):
    base_url, runs_root = gui_server

    for run_id in ("run-1", "run-2"):
        _write_json(runs_root / run_id / ".autodev" / "task_quality_index.json", {"final": {"status": "ok"}})
        _write_json(runs_root / run_id / ".autodev" / "task_final_last_validation.json", {"validation": []})
        _write_json(runs_root / run_id / ".autodev" / "run_trace.json", {"model": "m"})

    with request.urlopen(f"{base_url}/api/runs/compare?run_a=run-1&run_b=run-2", timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200

    assert body["left"]["run_id"] == "run-1"
    assert body["right"]["run_id"] == "run-2"
    assert body["left"]["totals"]["total_task_attempts"] == 0


def test_compare_endpoint_requires_both_run_ids(gui_server):
    base_url, _ = gui_server

    with pytest.raises(error.HTTPError) as excinfo:
        request.urlopen(f"{base_url}/api/runs/compare?left=run-1", timeout=5)

    assert excinfo.value.code == 400
    body = json.loads(excinfo.value.read().decode("utf-8"))
    assert body["error"]["code"] == "invalid_compare_query"


def test_role_resolution_header_precedence(monkeypatch):
    monkeypatch.setenv("AUTODEV_GUI_ROLE", "operator")
    assert _resolve_request_role({"X-Autodev-Role": "developer"}) == "developer"
    assert _resolve_request_role({"X-Autodev-Role": " evaluator "}) == "evaluator"


def test_role_resolution_env_and_default(monkeypatch):
    monkeypatch.setenv("AUTODEV_GUI_ROLE", "operator")
    assert _resolve_request_role({}) == "operator"
    monkeypatch.delenv("AUTODEV_GUI_ROLE", raising=False)
    assert _resolve_request_role({}) == "evaluator"


def _start_http_server(runs_root: Path):
    static_root = Path(__file__).resolve().parent.parent / "gui_mvp_static"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), GuiRequestHandler)
    httpd.config = GuiConfig(runs_root=runs_root, static_root=static_root)  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        return exc.code, body


@pytest.fixture
def gui_server(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    httpd, thread = _start_http_server(runs_root)
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield base_url, runs_root
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_start_endpoint_forbidden_by_default_role(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "out"), "profile": "enterprise"},
    )
    assert status == 403
    assert body["error"]["code"] == "forbidden_role"

    logs = sorted(audit_dir.glob("gui-audit-*.jsonl"))
    assert logs
    line = logs[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    event = json.loads(line)
    assert event["action"] == "start"
    assert event["result_status"] == "forbidden"


def test_start_endpoint_operator_dry_run_persists_audit(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "out"), "profile": "enterprise", "execute": False},
        headers={"X-Autodev-Role": "operator"},
    )
    assert status == 200
    assert body["spawned"] is False
    assert body["meta"]["audit_log_path"].endswith(".jsonl")

    logs = sorted(audit_dir.glob("gui-audit-*.jsonl"))
    line = logs[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    event = json.loads(line)
    assert event["result_status"] == "dry_run"
    assert event["payload"]["profile"] == "enterprise"


def test_start_endpoint_returns_500_when_audit_persist_fails(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_file = tmp_path / "audit-file"
    audit_file.write_text("not-a-dir", encoding="utf-8")
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_file))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "out"), "profile": "enterprise", "execute": False},
        headers={"X-Autodev-Role": "operator"},
    )
    assert status == 500
    assert body["error"]["code"] == "audit_persist_failed"


def test_resume_endpoint_requires_resumable_target(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")
    out = tmp_path / "not-resumable"
    out.mkdir(parents=True, exist_ok=True)

    status, body = _post_json(
        f"{base_url}/api/runs/resume",
        {"prd": str(prd), "out": str(out), "profile": "enterprise", "execute": False},
        headers={"X-Autodev-Role": "developer"},
    )
    assert status == 422
    assert body["error"]["code"] == "invalid_payload"
    assert "missing '.autodev/'" in body["error"]["message"]


def test_resume_endpoint_success_with_valid_markers(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    run_dir = tmp_path / "run-001"
    ad = run_dir / ".autodev"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "run_metadata.json").write_text(json.dumps({"run_id": "rid-001"}), encoding="utf-8")
    (ad / "checkpoint.json").write_text(
        json.dumps({"status": "running", "completed_task_ids": ["task-1"]}),
        encoding="utf-8",
    )

    status, body = _post_json(
        f"{base_url}/api/runs/resume",
        {"prd": str(prd), "out": str(run_dir), "profile": "enterprise", "execute": False},
        headers={"X-Autodev-Role": "developer"},
    )
    assert status == 200
    assert "--resume" in body["command"]
    assert body["resume_target"]["run_id"] == "rid-001"
