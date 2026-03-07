from __future__ import annotations

import json
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest

from autodev.gui_api import _reset_process_manager_for_tests
from autodev.gui_mvp_server import (
    GuiConfig,
    GuiRequestHandler,
    _list_runs,
    _quality_trends,
    _resolve_request_auth,
    _resolve_request_role,
    _run_compare,
    _run_detail,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_auth_config(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_process_manager_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "gui-process-state.json"
    monkeypatch.setenv("AUTODEV_GUI_PROCESS_STATE_FILE", str(state_file))
    monkeypatch.delenv("AUTODEV_GUI_LOCAL_SIMPLE", raising=False)
    monkeypatch.delenv("AUTODEV_GUI_ROLE", raising=False)
    _reset_process_manager_for_tests()


class _FakeProcess:
    _next_pid = 2000

    def __init__(self, *, terminate_timeout: bool = False, wait_returncode: int = 0):
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.terminate_timeout = terminate_timeout
        self.wait_returncode = wait_returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        if self.killed:
            return -9
        if self.terminated and not self.terminate_timeout:
            return self.wait_returncode
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.killed:
            return -9
        if self.terminated and self.terminate_timeout:
            raise subprocess.TimeoutExpired(cmd="autodev", timeout=timeout or 0)
        return self.wait_returncode


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


def test_gui_context_endpoint_defaults(gui_server):
    base_url, _ = gui_server

    with request.urlopen(f"{base_url}/api/gui/context", timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert body["defaults"]["out"]
    assert body["defaults"]["profile"]
    assert "prd" in body["defaults"]
    assert body["api"]["run_controls"] == ["start", "resume", "stop", "retry"]


def test_artifact_read_endpoint_returns_json_payload(gui_server):
    base_url, runs_root = gui_server
    run = runs_root / "run-artifact"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "run-artifact"})
    _write_json(run / ".autodev" / "task_final_last_validation.json", {"validation": [{"name": "ruff", "ok": False}]})

    status, body = _get_json(
        f"{base_url}/api/runs/run-artifact/artifacts/read?path=.autodev/task_final_last_validation.json"
    )

    assert status == 200
    assert body["content_type"] == "application/json"
    assert body["content"]["validation"][0]["name"] == "ruff"
    assert body["raw_content"].startswith('{"validation"')
    assert body["path"] == ".autodev/task_final_last_validation.json"


def test_artifact_read_endpoint_preserves_typed_malformed_json_error(gui_server):
    base_url, runs_root = gui_server
    run = runs_root / "run-bad"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "run-bad"})
    bad = run / ".autodev" / "plan.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"tasks": [', encoding="utf-8")

    status, body = _get_json(f"{base_url}/api/runs/run-bad/artifacts/read?path=plan.json")

    assert status == 200
    assert body["content_type"] == "application/json"
    assert body["content"] is None
    assert body["raw_content"] == '{"tasks": ['
    assert body["error"]["kind"] == "artifact_json_error"
    assert body["error"]["code"] == "artifact_json_malformed"


def test_artifact_read_endpoint_truncates_and_reports_typed_error(gui_server):
    base_url, runs_root = gui_server
    run = runs_root / "run-trunc"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "run-trunc"})
    _write_json(run / ".autodev" / "plan.json", {"tasks": [{"id": "a", "note": "x" * 200}]})

    status, body = _get_json(
        f"{base_url}/api/runs/run-trunc/artifacts/read?path=plan.json&max_bytes=32"
    )

    assert status == 200
    assert body["truncated"] is True
    assert body["content"] is None
    assert isinstance(body["raw_content"], str)
    assert body["raw_content"]
    assert body["error"]["code"] == "artifact_json_truncated"


def test_artifact_viewer_static_contract_includes_export_controls(gui_server):
    base_url, _ = gui_server

    with request.urlopen(f"{base_url}/index.html", timeout=5) as resp:
        index_html = resp.read().decode("utf-8")
    assert 'id="artifactCopyBtn"' in index_html
    assert 'id="artifactDownloadBtn"' in index_html
    assert 'id="artifactViewerActionStatus"' in index_html

    with request.urlopen(f"{base_url}/app.js", timeout=5) as resp:
        app_js = resp.read().decode("utf-8")
    assert "function getArtifactViewerTextPayload(payload)" in app_js
    assert "function copyTextToClipboard(text)" in app_js
    assert "function announceArtifactViewerAction(message, kind = 'ok')" in app_js
    assert "function withPreservedFocus(action)" in app_js
    assert "artifactViewerDownloadName" in app_js


def test_artifact_read_endpoint_rejects_invalid_query(gui_server):
    base_url, runs_root = gui_server
    run = runs_root / "run-invalid"
    _write_json(run / ".autodev" / "run_metadata.json", {"run_id": "run-invalid"})

    missing_status, missing_body = _get_json(f"{base_url}/api/runs/run-invalid/artifacts/read")
    assert missing_status == 400
    assert missing_body["error"]["code"] == "missing_artifact_path"

    invalid_status, invalid_body = _get_json(
        f"{base_url}/api/runs/run-invalid/artifacts/read?path=../../etc/passwd"
    )
    assert invalid_status == 422
    assert invalid_body["error"]["code"] == "invalid_artifact_request"


def test_artifact_read_endpoint_returns_404_for_unknown_run(gui_server):
    base_url, _ = gui_server

    status, body = _get_json(
        f"{base_url}/api/runs/not-found/artifacts/read?path=.autodev/task_quality_index.json"
    )
    assert status == 404
    assert body["error"]["code"] == "artifact_not_found"


def test_quality_trends_aggregates_validators_and_blockers(tmp_path):
    run_a = tmp_path / "run-a"
    _write_json(
        run_a / ".autodev" / "task_quality_index.json",
        {
            "unresolved_blockers": ["final_validation"],
            "final": {"status": "failed"},
        },
    )
    _write_json(
        run_a / ".autodev" / "task_final_last_validation.json",
        {
            "validation": [
                {"name": "ruff", "status": "failed", "ok": False, "blocking": True},
                {"name": "pytest", "status": "passed", "ok": True},
            ]
        },
    )

    run_b = tmp_path / "run-b"
    _write_json(
        run_b / ".autodev" / "task_quality_index.json",
        {
            "unresolved_blockers": [],
            "final": {"status": "ok"},
        },
    )
    _write_json(
        run_b / ".autodev" / "task_final_last_validation.json",
        {
            "results": [
                {"name": "ruff", "status": "passed", "ok": True},
            ]
        },
    )

    payload = _quality_trends(tmp_path, window=10)

    assert payload["counters"]["runs_total"] == 2
    assert payload["counters"]["runs_windowed"] == 2
    assert payload["counters"]["runs_included"] == 2
    assert payload["aggregates"]["validators"]["totals"]["total"] == 3
    assert payload["aggregates"]["validators"]["by_name"]["ruff"]["failed"] == 1
    assert payload["aggregates"]["validators"]["by_name"]["ruff"]["passed"] == 1
    assert payload["aggregates"]["validators"]["by_name"]["ruff"]["blocking_failed"] == 1
    assert payload["aggregates"]["blockers"]["total"] == 1
    assert payload["aggregates"]["blockers"]["by_name"]["final_validation"] == 1


def test_quality_trends_counts_sparse_missing_artifacts(tmp_path):
    valid = tmp_path / "run-valid"
    _write_json(valid / ".autodev" / "task_quality_index.json", {"final": {"status": "ok"}})
    _write_json(valid / ".autodev" / "task_final_last_validation.json", {"validation": []})

    missing_quality = tmp_path / "run-missing-quality"
    (missing_quality / ".autodev").mkdir(parents=True, exist_ok=True)
    _write_json(missing_quality / ".autodev" / "task_final_last_validation.json", {"validation": []})

    invalid_quality = tmp_path / "run-invalid-quality"
    (invalid_quality / ".autodev").mkdir(parents=True, exist_ok=True)
    (invalid_quality / ".autodev" / "task_quality_index.json").write_text('{"final": ', encoding="utf-8")
    _write_json(invalid_quality / ".autodev" / "task_final_last_validation.json", {"validation": []})

    missing_validation = tmp_path / "run-missing-validation"
    _write_json(missing_validation / ".autodev" / "task_quality_index.json", {"final": {"status": "ok"}})

    payload = _quality_trends(tmp_path, window=10)

    assert payload["counters"]["runs_total"] == 4
    assert payload["counters"]["runs_included"] == 1
    assert payload["counters"]["runs_skipped_missing_quality"] == 1
    assert payload["counters"]["runs_skipped_invalid_quality"] == 1
    assert payload["counters"]["runs_skipped_missing_validation"] == 1
    assert payload["counters"]["runs_skipped_invalid_validation"] == 0
    assert payload["counters"]["runs_skipped_missing_or_invalid_artifacts"] == 3


def test_trends_endpoint_supports_bounded_window(gui_server):
    base_url, runs_root = gui_server

    for idx in range(3):
        run = runs_root / f"run-{idx}"
        _write_json(run / ".autodev" / "task_quality_index.json", {"final": {"status": "ok"}})
        _write_json(run / ".autodev" / "task_final_last_validation.json", {"validation": []})

    with request.urlopen(f"{base_url}/api/runs/trends?window=2", timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert body["window"]["applied"] == 2
    assert body["counters"]["runs_total"] == 3
    assert body["counters"]["runs_windowed"] == 2
    assert len(body["runs"]) == 2


def test_quality_trends_partial_mode_includes_single_missing_artifact(tmp_path):
    only_validation = tmp_path / "run-only-validation"
    _write_json(only_validation / ".autodev" / "task_final_last_validation.json", {"validation": [{"name": "pytest", "status": "passed", "ok": True}]})

    only_quality = tmp_path / "run-only-quality"
    _write_json(
        only_quality / ".autodev" / "task_quality_index.json",
        {"final": {"status": "failed"}, "unresolved_blockers": ["final_validation"]},
    )

    strict_payload = _quality_trends(tmp_path, window=10, allow_partial=False)
    assert strict_payload["counters"]["runs_included"] == 0
    assert strict_payload["counters"]["runs_skipped_missing_or_invalid_artifacts"] == 2

    partial_payload = _quality_trends(tmp_path, window=10, allow_partial=True)
    assert partial_payload["mode"]["allow_partial"] is True
    assert partial_payload["counters"]["runs_included"] == 2
    assert partial_payload["counters"]["runs_included_partial"] == 2
    assert partial_payload["counters"]["runs_included_partial_missing_quality"] == 1
    assert partial_payload["counters"]["runs_included_partial_missing_validation"] == 1
    assert partial_payload["aggregates"]["validators"]["totals"]["passed"] == 1
    assert partial_payload["aggregates"]["blockers"]["by_name"]["final_validation"] == 1


def test_trends_endpoint_partial_query_flag(gui_server):
    base_url, runs_root = gui_server
    run = runs_root / "run-only-validation"
    _write_json(run / ".autodev" / "task_final_last_validation.json", {"validation": [{"name": "ruff", "status": "passed", "ok": True}]})

    with request.urlopen(f"{base_url}/api/runs/trends?window=5&partial=true", timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    assert resp.status == 200
    assert body["mode"]["allow_partial"] is True
    assert body["counters"]["runs_included"] == 1
    assert body["runs"][0]["inclusion_mode"] == "partial"


def test_role_resolution_header_precedence(monkeypatch):
    monkeypatch.setenv("AUTODEV_GUI_ROLE", "operator")
    assert _resolve_request_role({"X-Autodev-Role": "developer"}) == "developer"
    assert _resolve_request_role({"X-Autodev-Role": " evaluator "}) == "evaluator"


def test_role_resolution_env_and_default(monkeypatch):
    monkeypatch.setenv("AUTODEV_GUI_ROLE", "operator")
    assert _resolve_request_role({}) == "operator"
    monkeypatch.delenv("AUTODEV_GUI_ROLE", raising=False)
    monkeypatch.delenv("AUTODEV_GUI_LOCAL_SIMPLE", raising=False)
    assert _resolve_request_role({}) == "evaluator"


def test_role_resolution_local_simple_default(monkeypatch):
    monkeypatch.delenv("AUTODEV_GUI_ROLE", raising=False)
    monkeypatch.setenv("AUTODEV_GUI_LOCAL_SIMPLE", "1")
    assert _resolve_request_role({}) == "developer"


def test_auth_resolution_prefers_token_and_applies_scoped_policy(tmp_path, monkeypatch):
    cfg = _write_auth_config(
        tmp_path / "auth.json",
        {
            "tokens": {
                "tok-op": {"role": "operator", "subject": "svc-op"},
                "tok-dev": {"role": "developer", "subject": "svc-dev"},
            },
            "policies": [
                {
                    "name": "payments-prod",
                    "project": "payments",
                    "environment": "prod",
                    "actions": {"start": ["operator"]},
                }
            ],
        },
    )
    monkeypatch.setenv("AUTODEV_GUI_AUTH_CONFIG", str(cfg))

    denied = _resolve_request_auth(
        headers={"Authorization": "Bearer tok-dev", "X-Autodev-Role": "operator"},
        payload={"project": "payments", "environment": "prod"},
        action="start",
    )
    assert denied.source == "token"
    assert denied.subject == "svc-dev"
    assert denied.policy_name == "payments-prod"
    assert denied.role == "evaluator"

    allowed = _resolve_request_auth(
        headers={"Authorization": "Bearer tok-op", "X-Autodev-Role": "evaluator"},
        payload={"project": "payments", "environment": "prod"},
        action="start",
    )
    assert allowed.source == "token"
    assert allowed.role == "operator"


def test_auth_resolution_supports_session_cookie(tmp_path, monkeypatch):
    cfg = _write_auth_config(
        tmp_path / "auth.json",
        {
            "sessions": {
                "sess-001": {"role": "developer", "subject": "alice"},
            }
        },
    )
    monkeypatch.setenv("AUTODEV_GUI_AUTH_CONFIG", str(cfg))

    auth = _resolve_request_auth(
        headers={"Cookie": "foo=bar; autodev_session=sess-001"},
        payload={},
        action="resume",
    )
    assert auth.source == "session"
    assert auth.subject == "alice"
    assert auth.role == "developer"


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


def _get_json(url: str):
    try:
        with request.urlopen(url, timeout=5) as resp:
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
    assert isinstance(body["error"].get("fix_hints"), list)
    assert any("role" in hint.lower() or "policy" in hint.lower() for hint in body["error"]["fix_hints"])

    logs = sorted(audit_dir.glob("gui-audit-*.jsonl"))
    assert logs
    line = logs[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    event = json.loads(line)
    assert event["action"] == "start"
    assert event["result_status"] == "forbidden"


def test_start_endpoint_validation_error_contains_fix_hints(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {"out": str(tmp_path / "out"), "profile": "enterprise", "execute": False},
        headers={"X-Autodev-Role": "developer"},
    )
    assert status == 422
    assert body["error"]["code"] == "missing_prd"
    assert isinstance(body["error"].get("fix_hints"), list)
    assert any("PRD" in hint for hint in body["error"]["fix_hints"])


@pytest.mark.parametrize(
    ("payload_patch", "expected_code", "expected_field"),
    [
        ({"prd": ""}, "missing_prd", "prd"),
        ({"prd": "bad\npath"}, "invalid_prd", "prd"),
        ({"out": ""}, "missing_out", "out"),
        ({"out": "bad\nout"}, "invalid_out", "out"),
        ({"profile": "enterprise rm -rf"}, "invalid_profile", "profile"),
        ({"model": "openai/gpt 5"}, "invalid_model", "model"),
    ],
)
def test_start_endpoint_field_level_validation_matrix(
    gui_server,
    tmp_path,
    monkeypatch,
    payload_patch,
    expected_code,
    expected_field,
):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    payload = {
        "prd": str(prd),
        "out": str(tmp_path / "out"),
        "profile": "enterprise",
        "execute": False,
    }
    payload.update(payload_patch)

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        payload,
        headers={"X-Autodev-Role": "developer"},
    )

    assert status == 422
    assert body["error"]["code"] == expected_code
    assert body["error"]["field"] == expected_field


def test_start_endpoint_accepts_legacy_valid_payload_without_model(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {
            "prd": str(prd),
            "out": str(tmp_path / "out"),
            "profile": "enterprise",
            "execute": False,
        },
        headers={"X-Autodev-Role": "developer"},
    )

    assert status == 200
    assert body["spawned"] is False
    assert body["command"] == ["autodev", "--prd", str(prd), "--out", str(tmp_path / "out"), "--profile", "enterprise"]


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


def test_start_endpoint_accepts_bearer_token_role_and_scope_policy(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    auth_cfg = _write_auth_config(
        tmp_path / "auth.json",
        {
            "tokens": {
                "tok-operator": {"role": "operator", "subject": "svc-operator"},
            },
            "policies": [
                {
                    "name": "payments-prod",
                    "project": "payments",
                    "environment": "prod",
                    "actions": {"start": ["operator"]},
                }
            ],
        },
    )
    monkeypatch.setenv("AUTODEV_GUI_AUTH_CONFIG", str(auth_cfg))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {
            "prd": str(prd),
            "out": str(tmp_path / "out"),
            "profile": "enterprise",
            "project": "payments",
            "environment": "prod",
            "execute": False,
        },
        headers={"Authorization": "Bearer tok-operator"},
    )
    assert status == 200
    assert body["spawned"] is False

    logs = sorted(audit_dir.glob("gui-audit-*.jsonl"))
    event = json.loads(logs[-1].read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["role"] == "operator"
    assert event["auth"]["source"] == "token"
    assert event["auth"]["policy_name"] == "payments-prod"


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


def test_start_endpoint_scoped_policy_can_deny_token_role(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    auth_cfg = _write_auth_config(
        tmp_path / "auth.json",
        {
            "tokens": {
                "tok-dev": {"role": "developer", "subject": "svc-dev"},
            },
            "policies": [
                {
                    "name": "payments-prod",
                    "project": "payments",
                    "environment": "prod",
                    "actions": {"start": ["operator"]},
                }
            ],
        },
    )
    monkeypatch.setenv("AUTODEV_GUI_AUTH_CONFIG", str(auth_cfg))

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    status, body = _post_json(
        f"{base_url}/api/runs/start",
        {
            "prd": str(prd),
            "out": str(tmp_path / "out"),
            "profile": "enterprise",
            "project": "payments",
            "environment": "prod",
            "execute": False,
        },
        headers={"Authorization": "Bearer tok-dev"},
    )
    assert status == 403
    assert body["error"]["code"] == "forbidden_role"
    assert body["error"]["auth_source"] == "token"
    assert body["error"]["policy_allowed_roles"] == ["operator"]


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


def test_stop_and_retry_endpoints_happy_path(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    fakes = [_FakeProcess(terminate_timeout=False), _FakeProcess(terminate_timeout=False)]

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fakes.pop(0)

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    start_status, start_body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "out"), "profile": "enterprise", "execute": True},
        headers={"X-Autodev-Role": "operator"},
    )
    assert start_status == 200
    process_id = start_body["process"]["process_id"]

    retry_status, retry_body = _post_json(
        f"{base_url}/api/runs/retry",
        {"process_id": process_id, "execute": True},
        headers={"X-Autodev-Role": "operator"},
    )
    assert retry_status == 200
    assert retry_body["retry_of"] == process_id
    assert retry_body["process"]["retry_attempt"] == 2

    stop_status, stop_body = _post_json(
        f"{base_url}/api/runs/stop",
        {"process_id": process_id, "graceful_timeout_sec": 0.1},
        headers={"X-Autodev-Role": "operator"},
    )
    assert stop_status == 200
    assert stop_body["process"]["state"] in {"terminated", "exited"}


def test_stop_endpoint_unknown_process_returns_404(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    status, body = _post_json(
        f"{base_url}/api/runs/stop",
        {"process_id": "proc-missing", "graceful_timeout_sec": 0.1},
        headers={"X-Autodev-Role": "developer"},
    )
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_retry_endpoint_requires_process_or_run_id(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    status, body = _post_json(
        f"{base_url}/api/runs/retry",
        {"execute": False},
        headers={"X-Autodev-Role": "developer"},
    )
    assert status == 422
    assert body["error"]["code"] == "missing_retry_target"


def test_process_read_endpoints_list_detail_history(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    fake = _FakeProcess(terminate_timeout=False)

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    start_status, start_body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "runs" / "run-read"), "profile": "enterprise", "execute": True},
        headers={"X-Autodev-Role": "operator"},
    )
    assert start_status == 200
    process_id = start_body["process"]["process_id"]

    with request.urlopen(f"{base_url}/api/processes?limit=10", timeout=5) as resp:
        list_body = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert list_body["count"] == 1
    assert list_body["processes"][0]["process_id"] == process_id

    with request.urlopen(f"{base_url}/api/processes/{process_id}", timeout=5) as resp:
        detail_body = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert detail_body["run_link"]["run_id"] == "run-read"

    with request.urlopen(f"{base_url}/api/processes/{process_id}/history", timeout=5) as resp:
        history_body = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert history_body["process_id"] == process_id
    assert [row["state"] for row in history_body["history"]][:2] == ["spawned", "running"]


def test_process_panel_static_contract(gui_server):
    base_url, _ = gui_server

    with request.urlopen(f"{base_url}/index.html", timeout=5) as resp:
        index_html = resp.read().decode("utf-8")
    assert 'data-tab="processes"' in index_html
    assert 'id="tab-processes"' in index_html
    assert 'id="processList"' in index_html
    assert 'id="processStopBtn"' in index_html
    assert 'id="processClearFiltersBtn"' in index_html
    assert 'id="processPageNextBtn"' in index_html
    assert 'id="processPollingHint"' in index_html
    assert 'id="processStaleIndicator"' in index_html

    with request.urlopen(f"{base_url}/app.js", timeout=5) as resp:
        app_js = resp.read().decode("utf-8")
    assert "/api/processes?" in app_js
    assert "/api/processes/${encodeURIComponent(state.selectedProcessId)}" in app_js
    assert "function initProcessControls()" in app_js
    assert "function filterProcesses(rows)" in app_js
    assert "rowRunId.includes(runIdFilter)" in app_js
    assert "function renderProcessPagination(meta)" in app_js
    assert "function syncProcessActionButtons(process)" in app_js
    assert "state.processActionInFlight" in app_js
    assert "state.processLoadRequestSeq" in app_js
    assert "function noteProcessPollingSnapshot(rows, { source = 'manual' } = {})" in app_js
    assert "processPollBackoffExp" in app_js
    assert "processNextPollAtMs" in app_js
    assert "STALE • last transition" in app_js
    assert "Auto refresh " in app_js
    assert "No processes match current filters. Adjust filters and refresh." in app_js
    assert "No tracked processes yet. Start or retry a run to populate this panel." in app_js
    assert "if (requestSeq !== state.processLoadRequestSeq)" in app_js
    assert "source: 'poll'" in app_js
    assert "Another process action (" in app_js


def test_retry_endpoint_supports_run_id_target(gui_server, tmp_path, monkeypatch):
    base_url, _ = gui_server
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("AUTODEV_GUI_AUDIT_DIR", str(audit_dir))

    fakes = [_FakeProcess(terminate_timeout=False), _FakeProcess(terminate_timeout=False)]

    def _fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fakes.pop(0)

    monkeypatch.setattr("autodev.gui_process_manager.subprocess.Popen", _fake_popen)

    prd = tmp_path / "PRD.md"
    prd.write_text("# PRD", encoding="utf-8")

    start_status, start_body = _post_json(
        f"{base_url}/api/runs/start",
        {"prd": str(prd), "out": str(tmp_path / "runs" / "run-by-id"), "profile": "enterprise", "execute": True},
        headers={"X-Autodev-Role": "operator"},
    )
    assert start_status == 200

    retry_status, retry_body = _post_json(
        f"{base_url}/api/runs/retry",
        {"run_id": "run-by-id", "execute": True},
        headers={"X-Autodev-Role": "operator"},
    )
    assert retry_status == 200
    assert retry_body["retry_of"] == start_body["process"]["process_id"]
    assert retry_body["process"]["retry_root"] == start_body["process"]["retry_root"]
    assert retry_body["process"]["retry_attempt"] == 2
