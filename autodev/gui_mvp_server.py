from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .gui_api import GuiApiError, trigger_resume, trigger_start, validate_resume_target
from .gui_audit import persist_audit_event
from .gui_mvp_dto import normalize_run_trace, normalize_tasks, normalize_validation
from .run_status import normalize_run_status


@dataclass
class GuiConfig:
    runs_root: Path
    static_root: Path


ROLE_HEADER = "X-Autodev-Role"
ROLE_ENV = "AUTODEV_GUI_ROLE"
AUDIT_DIR_ENV = "AUTODEV_GUI_AUDIT_DIR"
READ_ONLY_ROLE = "evaluator"
MUTATING_ROLES = {"operator", "developer"}


def _resolve_request_role(headers: Any, env: dict[str, str] | None = None) -> str:
    env_map = env if env is not None else os.environ
    header_role = ""
    if headers is not None:
        header_val = headers.get(ROLE_HEADER)
        if isinstance(header_val, str):
            header_role = header_val.strip().lower()
    if header_role:
        return header_role

    env_role = env_map.get(ROLE_ENV, "")
    if isinstance(env_role, str) and env_role.strip():
        return env_role.strip().lower()

    return READ_ONLY_ROLE


def _is_mutation_allowed(role: str) -> bool:
    return role in MUTATING_ROLES


def _error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return payload


def _audit_payload_summary(payload: dict[str, Any], *, execute: bool) -> dict[str, Any]:
    return {
        "prd": str(payload.get("prd", "")),
        "out": str(payload.get("out", "")),
        "profile": str(payload.get("profile", "")),
        "model": str(payload.get("model", "")) if payload.get("model") is not None else None,
        "interactive": bool(payload.get("interactive", False)),
        "execute": bool(execute),
    }


def _append_audit_event(event: dict[str, Any]) -> Path:
    return persist_audit_event(event, audit_dir=os.environ.get(AUDIT_DIR_ENV))


def _load_json(path: Path) -> tuple[dict[str, Any] | list[Any] | None, dict[str, Any] | None]:
    if not path.exists():
        return None, None

    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, {
            "kind": "artifact_json_error",
            "code": "artifact_read_failed",
            "path": str(path),
            "message": str(exc),
        }
    except json.JSONDecodeError as exc:
        return None, {
            "kind": "artifact_json_error",
            "code": "artifact_json_malformed",
            "path": str(path),
            "message": exc.msg,
            "line": exc.lineno,
            "column": exc.colno,
            "position": exc.pos,
        }


def _run_status(quality: dict[str, Any] | None) -> str:
    return normalize_run_status(quality_index=quality, default="unknown")


def _list_runs(runs_root: Path) -> list[dict[str, Any]]:
    if not runs_root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for d in sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        quality, quality_error = _load_json(d / ".autodev" / "task_quality_index.json")
        run_trace, run_trace_error = _load_json(d / ".autodev" / "run_trace.json")
        trace_dto = normalize_run_trace(run_trace if isinstance(run_trace, dict) else None)

        profile = {}
        if isinstance(quality, dict):
            profile = quality.get("resolved_quality_profile", {}) if isinstance(quality.get("resolved_quality_profile"), dict) else {}

        artifact_errors = [err for err in [quality_error, run_trace_error] if err]
        rows.append(
            {
                "run_id": d.name,
                "path": str(d),
                "updated_at": datetime.fromtimestamp(d.stat().st_mtime).isoformat(),
                "status": _run_status(quality if isinstance(quality, dict) else None),
                "project_type": (quality or {}).get("project", {}).get("type", "") if isinstance(quality, dict) else "",
                "profile": profile.get("name", "") if isinstance(profile, dict) else "",
                "model": trace_dto.get("model", ""),
                "artifact_errors": artifact_errors,
            }
        )
    return rows


def _run_detail(run_dir: Path) -> dict[str, Any]:
    quality, quality_error = _load_json(run_dir / ".autodev" / "task_quality_index.json")
    final_validation, validation_error = _load_json(run_dir / ".autodev" / "task_final_last_validation.json")
    run_trace, run_trace_error = _load_json(run_dir / ".autodev" / "run_trace.json")
    updated_at = datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat()

    quality_dict = quality if isinstance(quality, dict) else {}
    final_dict = final_validation if isinstance(final_validation, dict) else {}
    trace_dict = run_trace if isinstance(run_trace, dict) else {}

    trace_dto = normalize_run_trace(trace_dict)
    tasks = normalize_tasks(quality_dict)
    validation_normalized = normalize_validation(final_dict, quality_dict)
    resolved_profile = quality_dict.get("resolved_quality_profile", {}) if isinstance(quality_dict.get("resolved_quality_profile"), dict) else {}

    artifact_errors = [err for err in [quality_error, validation_error, run_trace_error] if err]
    return {
        "run_id": run_dir.name,
        "status": _run_status(quality_dict),
        "updated_at": updated_at,
        "model": trace_dto.get("model", ""),
        "started_at": trace_dto.get("started_at", ""),
        "ended_at": trace_dto.get("completed_at", ""),
        "summary": {
            "project": quality_dict.get("project", {}),
            "totals": quality_dict.get("totals", {}),
            "final": quality_dict.get("final", {}),
            "profile": resolved_profile,
        },
        "metadata": {
            "model": trace_dto.get("model", ""),
            "profile": trace_dto.get("profile", "") or str(resolved_profile.get("name") or ""),
            "run_id": trace_dto.get("run_id", "") or run_dir.name,
            "request_id": trace_dto.get("request_id", ""),
            "started_at": trace_dto.get("started_at", ""),
            "completed_at": trace_dto.get("completed_at", ""),
            "total_elapsed_ms": trace_dto.get("total_elapsed_ms", 0),
            "event_count": trace_dto.get("event_count", 0),
            "phase_count": len(trace_dto.get("phase_timeline", [])),
        },
        "phase_timeline": trace_dto.get("phase_timeline", []),
        "tasks": tasks,
        "blockers": quality_dict.get("unresolved_blockers", []) if isinstance(quality_dict.get("unresolved_blockers", []), list) else [],
        "validation": final_dict,
        "validation_normalized": validation_normalized,
        "quality_index": quality_dict,
        "artifact_errors": artifact_errors,
    }


class GuiRequestHandler(BaseHTTPRequestHandler):
    server_version = "AutoDevGuiMvp/0.1"

    @property
    def config(self) -> GuiConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/runs":
            self._json_response({"runs": _list_runs(self.config.runs_root)})
            return

        if path.startswith("/api/runs/"):
            run_id = unquote(path.removeprefix("/api/runs/"))
            run_dir = self.config.runs_root / run_id
            if not run_id or not run_dir.exists() or not run_dir.is_dir():
                self._json_response({"error": "run not found", "run_id": run_id}, status=HTTPStatus.NOT_FOUND)
                return
            self._json_response(_run_detail(run_dir))
            return

        if path == "/healthz":
            self._json_response({"ok": True})
            return

        static_map = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
        }
        static_name = static_map.get(path)
        if not static_name:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        self._serve_static(static_name)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/runs/start":
            self._handle_run_control(action="start")
            return
        if path == "/api/runs/resume":
            self._handle_run_control(action="resume")
            return
        self._json_response({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_run_control(self, *, action: str) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return

        role = _resolve_request_role(self.headers)
        execute = bool(payload.get("execute", False))
        event = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "action": action,
            "role": role,
            "payload": _audit_payload_summary(payload, execute=execute),
        }

        if not _is_mutation_allowed(role):
            event["result_status"] = "forbidden"
            event["error"] = f"role '{role}' is not allowed to call mutating endpoints"
            self._audit_then_respond(
                body={
                    "error": _error_payload(
                        "forbidden_role",
                        f"Role '{role}' cannot perform '{action}'.",
                        role=role,
                        allowed_roles=sorted(MUTATING_ROLES),
                    )
                },
                status=HTTPStatus.FORBIDDEN,
                audit_event=event,
            )
            return

        validation_error = _validate_run_control_payload(payload, action=action)
        if validation_error:
            event["result_status"] = "invalid_request"
            event["error"] = validation_error["message"]
            self._audit_then_respond(
                body={"error": validation_error},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                audit_event=event,
            )
            return

        payload.pop("execute", None)

        try:
            if action == "resume":
                resume_info = validate_resume_target(str(payload.get("out", "")))
                result = trigger_resume(payload, execute=execute)
                result["resume_target"] = resume_info
            else:
                result = trigger_start(payload, execute=execute)
        except GuiApiError as exc:
            event["result_status"] = "invalid_request"
            event["error"] = str(exc)
            self._audit_then_respond(
                body={"error": _error_payload("invalid_payload", str(exc))},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                audit_event=event,
            )
            return
        except FileNotFoundError as exc:
            event["result_status"] = "not_found"
            event["error"] = str(exc)
            self._audit_then_respond(
                body={"error": _error_payload("not_found", str(exc))},
                status=HTTPStatus.NOT_FOUND,
                audit_event=event,
            )
            return
        except OSError as exc:
            event["result_status"] = "launch_failed"
            event["error"] = str(exc)
            self._audit_then_respond(
                body={"error": _error_payload("launch_failed", f"failed to launch autodev: {exc}")},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                audit_event=event,
            )
            return

        event["result_status"] = "spawned" if result.get("spawned") else "dry_run"
        self._audit_then_respond(body=result, status=HTTPStatus.OK, audit_event=event)

    def _read_json_payload(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_response({"error": "invalid Content-Length"}, status=HTTPStatus.BAD_REQUEST)
            return None

        if length <= 0:
            self._json_response({"error": "request body is required"}, status=HTTPStatus.BAD_REQUEST)
            return None

        try:
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response({"error": "request body must be valid JSON"}, status=HTTPStatus.BAD_REQUEST)
            return None

        if not isinstance(parsed, dict):
            self._json_response({"error": "request body must be a JSON object"}, status=HTTPStatus.BAD_REQUEST)
            return None

        return dict(parsed)

    def _audit_then_respond(self, *, body: dict[str, Any], status: HTTPStatus, audit_event: dict[str, Any]) -> None:
        try:
            audit_path = _append_audit_event(audit_event)
        except OSError as exc:
            self._json_response(
                {
                    "error": _error_payload(
                        "audit_persist_failed",
                        "request was processed but audit persistence failed",
                        detail=str(exc),
                    ),
                    "audit_event": audit_event,
                },
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        body.setdefault("meta", {})
        if isinstance(body["meta"], dict):
            body["meta"]["audit_log_path"] = str(audit_path)
        self._json_response(body, status=status)

    def _serve_static(self, filename: str) -> None:
        file_path = self.config.static_root / filename
        if not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Static file missing")
            return

        content_type = "text/plain; charset=utf-8"
        if filename.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif filename.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif filename.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"

        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_response(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _validate_run_control_payload(payload: dict[str, Any], *, action: str) -> dict[str, Any] | None:
    execute = payload.get("execute", False)
    if not isinstance(execute, bool):
        return _error_payload("invalid_execute", "'execute' must be a boolean")

    prd = payload.get("prd")
    if not isinstance(prd, str) or not prd.strip():
        return _error_payload("missing_prd", "'prd' is required")

    prd_path = Path(prd.strip()).expanduser()
    if not prd_path.is_file():
        return _error_payload("invalid_prd", "'prd' must point to an existing file")

    out = payload.get("out")
    if not isinstance(out, str) or not out.strip():
        return _error_payload("missing_out", "'out' is required")
    out_path = Path(out.strip()).expanduser()

    if out_path.exists() and not out_path.is_dir():
        return _error_payload("invalid_out", "'out' must be a directory path")

    if action == "resume" and not out_path.exists():
        return _error_payload(
            "resume_out_missing",
            "'out' must point to an existing run directory for resume",
        )

    profile = payload.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        return _error_payload("missing_profile", "'profile' is required")

    config_val = payload.get("config")
    if config_val is not None:
        if not isinstance(config_val, str) or not config_val.strip():
            return _error_payload("invalid_config", "'config' must be a non-empty string")
        config_path = Path(config_val.strip()).expanduser()
        if not config_path.is_file():
            return _error_payload("invalid_config", "'config' must point to an existing file")

    return None


def serve(host: str, port: int, runs_root: Path) -> None:
    static_root = Path(__file__).resolve().parent / "gui_mvp_static"
    config = GuiConfig(runs_root=runs_root.resolve(), static_root=static_root)

    httpd = ThreadingHTTPServer((host, port), GuiRequestHandler)
    httpd.config = config  # type: ignore[attr-defined]

    print(f"[gui-mvp] serving http://{host}:{port}")
    print(f"[gui-mvp] runs root: {config.runs_root}")
    httpd.serve_forever()


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="AutoDev GUI MVP static/API server")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8787, help="bind port (default: 8787)")
    ap.add_argument(
        "--runs-root",
        default="generated_runs",
        help="run directories root containing <run_id>/.autodev/* (default: generated_runs)",
    )
    return ap


def main() -> None:
    args = _build_parser().parse_args()
    runs_root = Path(args.runs_root)
    if not runs_root.is_absolute():
        runs_root = Path(os.getcwd()) / runs_root
    serve(args.host, args.port, runs_root)


if __name__ == "__main__":
    main()
