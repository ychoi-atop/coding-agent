from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .gui_artifact_schema import build_schema_marker, summarize_schema_markers
from .gui_process_manager import GuiRunProcessManager
from .run_status import normalize_run_status


AUTODEV_DIR = ".autodev"
RUN_METADATA_FILE = f"{AUTODEV_DIR}/run_metadata.json"
CHECKPOINT_FILE = f"{AUTODEV_DIR}/checkpoint.json"
RUN_TRACE_FILE = f"{AUTODEV_DIR}/run_trace.json"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:/@+-]+$")
_PROCESS_MANAGER = GuiRunProcessManager()


class GuiApiError(ValueError):
    """Raised for invalid request payloads or unsafe inputs."""


@dataclass(frozen=True)
class RunCommandRequest:
    prd: str
    out: str
    profile: str
    model: str | None = None
    interactive: bool = False
    config: str | None = None


# ---------------------------------------------------------------------------
# Filesystem readers (Step 1: run/artifact read API scaffolding)
# ---------------------------------------------------------------------------


def list_runs(out_root: str, limit: int = 50) -> list[dict[str, Any]]:
    root = Path(out_root).expanduser().resolve()
    if not root.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue

        metadata, metadata_error = _read_json_with_error(run_dir / RUN_METADATA_FILE)
        checkpoint, checkpoint_error = _read_json_with_error(run_dir / CHECKPOINT_FILE)
        status = _derive_status(metadata, checkpoint)

        run_id = _coerce_str(metadata.get("run_id")) if isinstance(metadata, dict) else ""
        request_id = _coerce_str(metadata.get("request_id")) if isinstance(metadata, dict) else ""
        profile = _coerce_str(metadata.get("requested_profile")) if isinstance(metadata, dict) else ""
        llm_model = ""
        if isinstance(metadata, dict):
            llm = metadata.get("llm")
            if isinstance(llm, dict):
                llm_model = _coerce_str(llm.get("model"))

        started_at = _infer_started_at(run_dir, metadata)
        completed_at = _coerce_str(metadata.get("run_completed_at")) if isinstance(metadata, dict) else ""
        artifact_errors = [err for err in [metadata_error, checkpoint_error] if err]
        schema_versions, schema_warnings = summarize_schema_markers(
            {
                "run_metadata": metadata,
                "checkpoint": checkpoint,
            }
        )

        rows.append(
            {
                "run_id": run_id,
                "request_id": request_id,
                "run_dir": str(run_dir),
                "run_name": run_dir.name,
                "status": status,
                "profile": profile,
                "model": llm_model,
                "started_at": started_at,
                "completed_at": completed_at,
                "artifact_errors": artifact_errors,
                "artifact_schema_versions": schema_versions,
                "artifact_schema_warnings": schema_warnings,
            }
        )

    rows.sort(
        key=lambda row: (
            _sortable_ts(row.get("started_at")),
            row.get("run_name", ""),
        ),
        reverse=True,
    )
    return rows[: max(1, limit)]


def get_run_detail(out_root: str, run_key: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(out_root, run_key)
    metadata, metadata_error = _read_json_with_error(run_dir / RUN_METADATA_FILE)
    checkpoint, checkpoint_error = _read_json_with_error(run_dir / CHECKPOINT_FILE)
    run_trace, run_trace_error = _read_json_with_error(run_dir / f"{AUTODEV_DIR}/run_trace.json")

    if metadata is None and checkpoint is None and run_trace is None:
        raise FileNotFoundError(f"No .autodev artifacts found for run: {run_key}")

    status = _derive_status(metadata, checkpoint)
    artifact_errors = [err for err in [metadata_error, checkpoint_error, run_trace_error] if err]
    schema_versions, schema_warnings = summarize_schema_markers(
        {
            "run_metadata": metadata,
            "checkpoint": checkpoint,
            "run_trace": run_trace,
        }
    )
    return {
        "run_id": _coerce_str((metadata or {}).get("run_id")) or run_dir.name,
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "status": status,
        "run_metadata": metadata,
        "checkpoint": checkpoint,
        "run_trace": run_trace,
        "artifact_errors": artifact_errors,
        "artifact_schema_versions": schema_versions,
        "artifact_schema_warnings": schema_warnings,
    }


def read_artifact(
    out_root: str,
    run_key: str,
    artifact_rel_path: str,
    *,
    max_bytes: int = 512_000,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(out_root, run_key)
    safe_rel = _normalize_artifact_path(artifact_rel_path)
    artifact_path = (run_dir / safe_rel).resolve()

    if not artifact_path.is_file():
        raise FileNotFoundError(f"Artifact not found: {safe_rel}")

    raw = artifact_path.read_bytes()
    truncated = len(raw) > max_bytes
    payload = raw[:max_bytes]

    content_type = "text/plain"
    content: Any
    parse_error: dict[str, Any] | None = None
    if artifact_path.suffix == ".json":
        content_type = "application/json"
        text = payload.decode("utf-8", errors="replace")
        try:
            content = json.loads(text)
        except json.JSONDecodeError as exc:
            content = None
            parse_error = _build_json_parse_error_payload(path=safe_rel, err=exc, truncated=truncated)
    elif artifact_path.suffix == ".md":
        content_type = "text/markdown"
        content = payload.decode("utf-8")
    else:
        content = payload.decode("utf-8", errors="replace")

    result = {
        "run_name": run_dir.name,
        "path": str(safe_rel),
        "content_type": content_type,
        "truncated": truncated,
        "content": content,
    }
    if parse_error:
        result["error"] = parse_error

    artifact_name = _artifact_name_from_path(safe_rel)
    if artifact_name is not None:
        marker, warning = build_schema_marker(artifact_name, content)
        result["artifact_schema"] = marker
        if warning is not None:
            result["warning"] = warning

    return result


# ---------------------------------------------------------------------------
# Run control wrappers (Step 2: start/resume trigger scaffolding)
# ---------------------------------------------------------------------------


def build_start_command(payload: Mapping[str, Any]) -> list[str]:
    req = _parse_command_request(payload)
    cmd = [
        "autodev",
        "--prd",
        _safe_path_arg(req.prd, "prd"),
        "--out",
        _safe_path_arg(req.out, "out"),
        "--profile",
        _safe_token(req.profile, "profile"),
    ]
    if req.model:
        cmd.extend(["--model", _safe_token(req.model, "model")])
    if req.interactive:
        cmd.append("--interactive")
    if req.config:
        cmd.extend(["--config", _safe_path_arg(req.config, "config")])
    return cmd


def build_resume_command(payload: Mapping[str, Any]) -> list[str]:
    cmd = build_start_command(payload)
    if "--resume" not in cmd:
        cmd.append("--resume")
    return cmd


def validate_resume_target(out_dir: str) -> dict[str, Any]:
    target = Path(out_dir).expanduser().resolve()
    if not target.exists():
        raise GuiApiError("'out' must point to an existing run directory for resume")
    if not target.is_dir():
        raise GuiApiError("'out' must be a run directory path for resume")

    ad = target / AUTODEV_DIR
    if not ad.is_dir():
        raise GuiApiError("resume target is missing '.autodev/' metadata directory")

    checkpoint_path = ad / "checkpoint.json"
    run_metadata_path = ad / "run_metadata.json"
    run_trace_path = ad / "run_trace.json"

    if not checkpoint_path.is_file():
        raise GuiApiError("resume target is missing '.autodev/checkpoint.json'")
    if not run_metadata_path.is_file():
        raise GuiApiError("resume target is missing '.autodev/run_metadata.json'")

    checkpoint, checkpoint_error = _read_json_with_error(checkpoint_path)
    metadata, metadata_error = _read_json_with_error(run_metadata_path)
    _, run_trace_error = _read_json_with_error(run_trace_path)

    if checkpoint_error:
        raise GuiApiError(
            f"checkpoint is not readable JSON ({checkpoint_error.get('code', 'artifact_read_failed')})"
        )
    if metadata_error:
        raise GuiApiError(
            f"run metadata is not readable JSON ({metadata_error.get('code', 'artifact_read_failed')})"
        )

    if not isinstance(checkpoint, dict):
        raise GuiApiError("checkpoint payload must be a JSON object")
    if not isinstance(metadata, dict):
        raise GuiApiError("run metadata payload must be a JSON object")

    run_id = _coerce_str(metadata.get("run_id"))
    if not run_id:
        raise GuiApiError("run metadata is missing 'run_id'; target is not resumable")

    checkpoint_run_id = _coerce_str(checkpoint.get("run_id"))
    if checkpoint_run_id and checkpoint_run_id != run_id:
        raise GuiApiError(
            "checkpoint/run metadata mismatch: 'run_id' values differ; verify selected run directory"
        )

    completed_task_ids = checkpoint.get("completed_task_ids")
    failed_task_id = checkpoint.get("failed_task_id")
    has_resumable_markers = isinstance(completed_task_ids, list) or failed_task_id is not None
    if not has_resumable_markers:
        raise GuiApiError(
            "checkpoint is missing resumable markers ('completed_task_ids' or 'failed_task_id')"
        )

    status = normalize_run_status(metadata=metadata, checkpoint=checkpoint, default="unknown")
    if status in {"ok", "failed"}:
        raise GuiApiError(
            "resume target appears finalized (status is terminal); choose an in-progress run checkpoint"
        )

    artifact_errors = [err for err in [run_trace_error] if err]
    return {
        "run_dir": str(target),
        "run_id": run_id,
        "status": status,
        "completed_task_count": len(completed_task_ids) if isinstance(completed_task_ids, list) else 0,
        "artifact_errors": artifact_errors,
    }


def trigger_start(payload: Mapping[str, Any], *, execute: bool = False) -> dict[str, Any]:
    return _trigger(payload, resume=False, execute=execute)


def trigger_resume(payload: Mapping[str, Any], *, execute: bool = False) -> dict[str, Any]:
    return _trigger(payload, resume=True, execute=execute)


def trigger_stop(payload: Mapping[str, Any], *, graceful_timeout_sec: float = 2.0) -> dict[str, Any]:
    process_id = _required_str(payload, "process_id")
    if graceful_timeout_sec <= 0:
        raise GuiApiError("'graceful_timeout_sec' must be greater than zero")
    try:
        process = _PROCESS_MANAGER.stop(process_id, graceful_timeout_sec=graceful_timeout_sec)
    except KeyError as exc:
        raise FileNotFoundError(f"Unknown process_id: {process_id}") from exc

    return {
        "ok": True,
        "stopped": process.get("state") in {"terminated", "killed", "exited"},
        "process": process,
        "audit_event": {
            "action": "stop",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "process_id": process_id,
            "graceful_timeout_sec": graceful_timeout_sec,
            "result_state": process.get("state"),
        },
    }


def trigger_retry(payload: Mapping[str, Any], *, execute: bool = False) -> dict[str, Any]:
    process_id = _optional_str(payload, "process_id")
    run_id = _optional_str(payload, "run_id")
    if not process_id and not run_id:
        raise GuiApiError("'process_id' or 'run_id' is required")

    try:
        result = _PROCESS_MANAGER.retry(process_id=process_id, run_id=run_id, execute=execute)
    except KeyError as exc:
        if process_id:
            raise FileNotFoundError(f"Unknown process_id: {process_id}") from exc
        raise FileNotFoundError(f"Unknown run_id: {run_id}") from exc

    retry_of = result.get("retry_of")
    event = {
        "action": "retry",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "process_id": process_id,
        "run_id": run_id,
        "retry_of": retry_of,
        "spawned": result.get("spawned", False),
    }
    if isinstance(result.get("process"), dict):
        event["new_process_id"] = result["process"].get("process_id")
        event["retry_root"] = result["process"].get("retry_root")
        event["retry_attempt"] = result["process"].get("retry_attempt")

    result["audit_event"] = event
    return result


def list_processes(*, limit: int = 100, state: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    rows = _PROCESS_MANAGER.list(limit=limit, state=state, run_id=run_id)
    return {"processes": rows, "count": len(rows)}


def get_process_detail(process_id: str) -> dict[str, Any]:
    process = _PROCESS_MANAGER.get(process_id)
    if process is None:
        raise FileNotFoundError(f"Unknown process_id: {process_id}")
    return process


def get_process_history(process_id: str) -> dict[str, Any]:
    process = _PROCESS_MANAGER.get(process_id)
    if process is None:
        raise FileNotFoundError(f"Unknown process_id: {process_id}")
    try:
        transitions = _PROCESS_MANAGER.history(process_id)
    except KeyError as exc:
        raise FileNotFoundError(f"Unknown process_id: {process_id}") from exc
    return {
        "process_id": process_id,
        "state": process.get("state"),
        "retry_root": process.get("retry_root"),
        "retry_attempt": process.get("retry_attempt"),
        "history": transitions,
    }


def _trigger(
    payload: Mapping[str, Any],
    *,
    resume: bool,
    execute: bool,
) -> dict[str, Any]:
    cmd = build_resume_command(payload) if resume else build_start_command(payload)
    event = {
        "action": "resume" if resume else "start",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": cmd,
    }

    if not execute:
        return {"ok": True, "spawned": False, "command": cmd, "audit_event": event}

    payload_dict = dict(payload)
    run_link = {"out": payload_dict.get("out", "")}
    process = _PROCESS_MANAGER.spawn(
        action=event["action"],
        payload=payload_dict,
        command=cmd,
        run_link=run_link,
    )
    event["process_id"] = process.get("process_id")
    event["state"] = process.get("state")

    return {
        "ok": True,
        "spawned": True,
        "pid": process.get("pid"),
        "process": process,
        "command": cmd,
        "audit_event": event,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_process_manager_for_tests() -> None:
    global _PROCESS_MANAGER
    _PROCESS_MANAGER = GuiRunProcessManager()


def _read_json_optional(path: Path) -> dict[str, Any] | None:
    data, _ = _read_json_with_error(path)
    return data


def _read_json_with_error(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not path.is_file():
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
        return None, _build_json_parse_error_payload(path=path, err=exc)


def _build_json_parse_error_payload(path: Path, err: json.JSONDecodeError, *, truncated: bool = False) -> dict[str, Any]:
    return {
        "kind": "artifact_json_error",
        "code": "artifact_json_truncated" if truncated else "artifact_json_malformed",
        "path": str(path),
        "message": err.msg,
        "line": err.lineno,
        "column": err.colno,
        "position": err.pos,
    }


def _derive_status(metadata: dict[str, Any] | None, checkpoint: dict[str, Any] | None) -> str:
    return normalize_run_status(metadata=metadata, checkpoint=checkpoint, default="running")


def _infer_started_at(run_dir: Path, metadata: dict[str, Any] | None) -> str:
    if isinstance(metadata, dict):
        raw = _coerce_str(metadata.get("run_started_at"))
        if raw:
            return raw
    return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _sortable_ts(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return ""


def _resolve_run_dir(out_root: str, run_key: str) -> Path:
    root = Path(out_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Run output root not found: {out_root}")

    direct = (root / run_key).resolve()
    if direct.is_dir() and direct.parent == root:
        return direct

    # fallback: find by run_id in .autodev/run_metadata.json
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        meta = _read_json_optional(run_dir / RUN_METADATA_FILE)
        if isinstance(meta, dict) and _coerce_str(meta.get("run_id")) == run_key:
            return run_dir

    raise FileNotFoundError(f"Run not found: {run_key}")


def _normalize_artifact_path(artifact_rel_path: str) -> Path:
    rel = artifact_rel_path.strip().replace("\\", "/")
    if not rel:
        raise GuiApiError("artifact path is required")
    if rel.startswith("/"):
        raise GuiApiError("artifact path must be relative")

    if not rel.startswith(f"{AUTODEV_DIR}/"):
        rel = f"{AUTODEV_DIR}/{rel}"

    candidate = Path(rel)
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise GuiApiError("artifact path contains invalid traversal segment")
    if not str(candidate).startswith(f"{AUTODEV_DIR}/"):
        raise GuiApiError("artifact path must stay under .autodev/")
    return candidate


def _artifact_name_from_path(path: Path) -> str | None:
    filename = path.name
    mapping = {
        "run_metadata.json": "run_metadata",
        "checkpoint.json": "checkpoint",
        "run_trace.json": "run_trace",
        "task_quality_index.json": "task_quality_index",
        "task_final_last_validation.json": "task_final_last_validation",
    }
    return mapping.get(filename)


def _parse_command_request(payload: Mapping[str, Any]) -> RunCommandRequest:
    return RunCommandRequest(
        prd=_required_str(payload, "prd"),
        out=_required_str(payload, "out"),
        profile=_required_str(payload, "profile"),
        model=_optional_str(payload, "model"),
        interactive=bool(payload.get("interactive", False)),
        config=_optional_str(payload, "config"),
    )


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    val = payload.get(key)
    if not isinstance(val, str) or not val.strip():
        raise GuiApiError(f"'{key}' is required")
    return val.strip()


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    val = payload.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise GuiApiError(f"'{key}' must be a string")
    trimmed = val.strip()
    return trimmed or None


def _safe_path_arg(value: str, label: str) -> str:
    if any(ch in value for ch in ["\x00", "\n", "\r"]):
        raise GuiApiError(f"'{label}' contains unsafe characters")
    return value


def _safe_token(value: str, label: str) -> str:
    if not _SAFE_TOKEN_RE.fullmatch(value):
        raise GuiApiError(f"'{label}' contains unsupported characters")
    return value


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
