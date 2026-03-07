from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .gui_api import (
    GuiApiError,
    get_process_detail,
    get_process_history,
    list_processes,
    read_artifact,
    trigger_resume,
    trigger_retry,
    trigger_start,
    trigger_stop,
    validate_resume_target,
)
from .gui_artifact_schema import summarize_schema_markers
from .gui_audit import persist_audit_event
from .gui_failure_hints import build_run_control_fix_hints
from .gui_mvp_dto import normalize_run_comparison_summary, normalize_run_trace, normalize_tasks, normalize_validation
from .run_status import normalize_run_status


@dataclass
class GuiConfig:
    runs_root: Path
    static_root: Path
    local_simple_mode: bool = False
    default_profile: str = "enterprise"
    default_config_path: str = ""
    default_prd_path: str = ""


ROLE_HEADER = "X-Autodev-Role"
ROLE_ENV = "AUTODEV_GUI_ROLE"
AUDIT_DIR_ENV = "AUTODEV_GUI_AUDIT_DIR"
AUTH_CONFIG_ENV = "AUTODEV_GUI_AUTH_CONFIG"
LOCAL_SIMPLE_ENV = "AUTODEV_GUI_LOCAL_SIMPLE"
DEFAULT_PROFILE_ENV = "AUTODEV_GUI_DEFAULT_PROFILE"
DEFAULT_CONFIG_ENV = "AUTODEV_GUI_DEFAULT_CONFIG"
DEFAULT_PRD_ENV = "AUTODEV_GUI_DEFAULT_PRD"
TOKEN_HEADER = "X-Autodev-Token"
SESSION_HEADER = "X-Autodev-Session"
AUTHORIZATION_HEADER = "Authorization"
SESSION_COOKIE_NAME = "autodev_session"
READ_ONLY_ROLE = "evaluator"
MUTATING_ROLES = {"operator", "developer"}

DEFAULT_TREND_WINDOW = 20
MAX_TREND_WINDOW = 200
DEFAULT_ARTIFACT_READ_MAX_BYTES = 512_000
MAX_ARTIFACT_READ_MAX_BYTES = 2_000_000
SAFE_RUN_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:/@+-]+$")


@dataclass(frozen=True)
class AuthContext:
    role: str
    source: str
    subject: str | None = None
    scope: dict[str, str] | None = None
    policy_name: str | None = None
    policy_allowed_roles: list[str] | None = None


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

    if _is_local_simple_mode(env_map):
        return "developer"

    return READ_ONLY_ROLE


def _is_mutation_allowed(role: str) -> bool:
    return role in MUTATING_ROLES


def _is_local_simple_mode(env: dict[str, str] | None = None) -> bool:
    env_map = env if env is not None else os.environ
    raw = str(env_map.get(LOCAL_SIMPLE_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_request_auth(
    *,
    headers: Any,
    payload: dict[str, Any],
    action: str,
    env: dict[str, str] | None = None,
) -> AuthContext:
    env_map = env if env is not None else os.environ
    auth_cfg = _load_auth_config(env_map)
    scope = _resolve_policy_scope(payload, env_map)

    token = _extract_bearer_token(headers) or _extract_header_value(headers, TOKEN_HEADER)
    if token:
        token_entry = _lookup_auth_principal(auth_cfg.get("tokens"), token)
        if token_entry is not None:
            role = _coerce_auth_role(token_entry)
            return _apply_scoped_policy(
                role=role,
                action=action,
                scope=scope,
                auth_cfg=auth_cfg,
                source="token",
                subject=_coerce_auth_subject(token_entry, token),
            )

    session_id = _extract_header_value(headers, SESSION_HEADER) or _extract_session_cookie(headers)
    if session_id:
        session_entry = _lookup_auth_principal(auth_cfg.get("sessions"), session_id)
        if session_entry is not None:
            role = _coerce_auth_role(session_entry)
            return _apply_scoped_policy(
                role=role,
                action=action,
                scope=scope,
                auth_cfg=auth_cfg,
                source="session",
                subject=_coerce_auth_subject(session_entry, session_id),
            )

    role = _resolve_request_role(headers, env=env_map)
    return _apply_scoped_policy(
        role=role,
        action=action,
        scope=scope,
        auth_cfg=auth_cfg,
        source="header_or_env",
        subject=None,
    )


def _extract_header_value(headers: Any, key: str) -> str:
    if headers is None:
        return ""
    value = headers.get(key)
    return value.strip() if isinstance(value, str) else ""


def _extract_bearer_token(headers: Any) -> str:
    raw = _extract_header_value(headers, AUTHORIZATION_HEADER)
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _extract_session_cookie(headers: Any) -> str:
    raw_cookie = _extract_header_value(headers, "Cookie")
    if not raw_cookie:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(raw_cookie)
    except Exception:
        return ""
    morsel = jar.get(SESSION_COOKIE_NAME)
    if morsel is None:
        return ""
    return morsel.value.strip()


def _load_auth_config(env_map: dict[str, str]) -> dict[str, Any]:
    cfg_path_raw = env_map.get(AUTH_CONFIG_ENV, "")
    if not isinstance(cfg_path_raw, str) or not cfg_path_raw.strip():
        return {}
    cfg_path = Path(cfg_path_raw.strip()).expanduser()
    if not cfg_path.is_file():
        return {}
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _lookup_auth_principal(container: Any, key: str) -> Any:
    if not isinstance(container, dict):
        return None
    entry = container.get(key)
    if entry is None:
        return None
    if isinstance(entry, str):
        return {"role": entry, "subject": key}
    if isinstance(entry, dict):
        return entry
    return None


def _coerce_auth_role(entry: Any) -> str:
    if isinstance(entry, dict):
        role_val = entry.get("role")
        if isinstance(role_val, str) and role_val.strip():
            return role_val.strip().lower()
    return READ_ONLY_ROLE


def _coerce_auth_subject(entry: Any, fallback: str) -> str:
    if isinstance(entry, dict):
        subject = entry.get("subject")
        if isinstance(subject, str) and subject.strip():
            return subject.strip()
    return fallback


def _resolve_policy_scope(payload: dict[str, Any], env_map: dict[str, str]) -> dict[str, str]:
    project = ""
    environment = ""

    project_val = payload.get("project")
    if isinstance(project_val, str) and project_val.strip():
        project = project_val.strip()
    env_val = payload.get("environment")
    if isinstance(env_val, str) and env_val.strip():
        environment = env_val.strip()

    if not project:
        scoped_project = env_map.get("AUTODEV_GUI_PROJECT", "")
        if isinstance(scoped_project, str) and scoped_project.strip():
            project = scoped_project.strip()
    if not environment:
        scoped_env = env_map.get("AUTODEV_GUI_ENVIRONMENT", "")
        if isinstance(scoped_env, str) and scoped_env.strip():
            environment = scoped_env.strip()

    return {
        "project": project,
        "environment": environment,
    }


def _apply_scoped_policy(
    *,
    role: str,
    action: str,
    scope: dict[str, str],
    auth_cfg: dict[str, Any],
    source: str,
    subject: str | None,
) -> AuthContext:
    policy = _select_policy(auth_cfg.get("policies"), scope)
    if policy is None and isinstance(auth_cfg.get("default_policy"), dict):
        policy = auth_cfg["default_policy"]

    allowed_roles = _policy_allowed_roles(policy, action)
    effective_role = role
    if allowed_roles is not None and role not in allowed_roles:
        effective_role = READ_ONLY_ROLE

    policy_name = None
    if isinstance(policy, dict):
        name = policy.get("name")
        if isinstance(name, str) and name.strip():
            policy_name = name.strip()

    return AuthContext(
        role=effective_role,
        source=source,
        subject=subject,
        scope=scope,
        policy_name=policy_name,
        policy_allowed_roles=sorted(allowed_roles) if allowed_roles is not None else None,
    )


def _select_policy(policies: Any, scope: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(policies, list):
        return None

    best: tuple[int, dict[str, Any]] | None = None
    for row in policies:
        if not isinstance(row, dict):
            continue
        matched, score = _policy_matches_scope(row, scope)
        if not matched:
            continue
        if best is None or score > best[0]:
            best = (score, row)

    return best[1] if best else None


def _policy_matches_scope(policy: dict[str, Any], scope: dict[str, str]) -> tuple[bool, int]:
    project = scope.get("project", "")
    environment = scope.get("environment", "")

    project_match = _match_scope_value(policy, "project", "projects", project)
    if project_match is None:
        return False, 0
    env_match = _match_scope_value(policy, "environment", "environments", environment)
    if env_match is None:
        return False, 0

    score = 0
    if project_match:
        score += 2
    if env_match:
        score += 1
    return True, score


def _match_scope_value(policy: dict[str, Any], singular_key: str, plural_key: str, current: str) -> bool | None:
    singular = policy.get(singular_key)
    plural = policy.get(plural_key)
    configured: list[str] = []

    if isinstance(singular, str) and singular.strip():
        configured.append(singular.strip())
    if isinstance(plural, list):
        configured.extend([str(v).strip() for v in plural if str(v).strip()])

    if not configured:
        return False
    if not current:
        return None
    return current in configured


def _policy_allowed_roles(policy: dict[str, Any] | None, action: str) -> set[str] | None:
    if not isinstance(policy, dict):
        return None

    actions = policy.get("actions")
    if isinstance(actions, dict):
        action_roles = actions.get(action)
        parsed = _parse_allowed_roles(action_roles)
        if parsed is not None:
            return parsed

    return _parse_allowed_roles(policy.get("allowed_roles"))


def _parse_allowed_roles(raw: Any) -> set[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return set()
    out = {str(v).strip().lower() for v in raw if str(v).strip()}
    return out


def _contains_unsafe_path_chars(value: str) -> bool:
    return any(ch in value for ch in ("\x00", "\n", "\r"))


def _is_supported_run_token(value: str) -> bool:
    return bool(SAFE_RUN_TOKEN_RE.fullmatch(value))


def _coerce_correlation_id_for_audit(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized:
        return ""
    if not _is_supported_run_token(normalized):
        return ""
    return normalized


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
        "process_id": str(payload.get("process_id", "")),
        "run_id": str(payload.get("run_id", "")),
        "correlation_id": str(payload.get("correlation_id", "")),
        "graceful_timeout_sec": payload.get("graceful_timeout_sec", None),
        "project": str(payload.get("project", "")),
        "environment": str(payload.get("environment", "")),
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
        schema_versions, schema_warnings = summarize_schema_markers(
            {
                "task_quality_index": quality,
                "run_trace": run_trace,
            }
        )
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
                "artifact_schema_versions": schema_versions,
                "artifact_schema_warnings": schema_warnings,
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
    schema_versions, schema_warnings = summarize_schema_markers(
        {
            "task_quality_index": quality,
            "task_final_last_validation": final_validation,
            "run_trace": run_trace,
        }
    )
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
        "artifact_schema_versions": schema_versions,
        "artifact_schema_warnings": schema_warnings,
    }


def _run_compare(runs_root: Path, left_run_id: str, right_run_id: str) -> tuple[dict[str, Any], HTTPStatus]:
    left_id = left_run_id.strip()
    right_id = right_run_id.strip()

    if not left_id or not right_id:
        return {
            "error": {
                "code": "invalid_compare_query",
                "message": "query params 'left' and 'right' are required",
            }
        }, HTTPStatus.BAD_REQUEST

    left_dir = runs_root / left_id
    right_dir = runs_root / right_id

    if not left_dir.exists() or not left_dir.is_dir():
        return {"error": "run not found", "side": "left", "run_id": left_id}, HTTPStatus.NOT_FOUND
    if not right_dir.exists() or not right_dir.is_dir():
        return {"error": "run not found", "side": "right", "run_id": right_id}, HTTPStatus.NOT_FOUND

    left_summary = normalize_run_comparison_summary(_run_detail(left_dir))
    right_summary = normalize_run_comparison_summary(_run_detail(right_dir))

    return {
        "left": left_summary,
        "right": right_summary,
        "delta": {
            "total_task_attempts": right_summary["totals"]["total_task_attempts"] - left_summary["totals"]["total_task_attempts"],
            "hard_failures": right_summary["totals"]["hard_failures"] - left_summary["totals"]["hard_failures"],
            "soft_failures": right_summary["totals"]["soft_failures"] - left_summary["totals"]["soft_failures"],
            "blocker_count": right_summary["totals"]["blocker_count"] - left_summary["totals"]["blocker_count"],
            "validation_failed": right_summary["validation"]["failed"] - left_summary["validation"]["failed"],
            "validation_passed": right_summary["validation"]["passed"] - left_summary["validation"]["passed"],
            "timeline_total_duration_ms": right_summary["timeline"]["total_duration_ms"] - left_summary["timeline"]["total_duration_ms"],
        },
    }, HTTPStatus.OK


def _parse_trend_window(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_TREND_WINDOW
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_TREND_WINDOW
    if parsed < 1:
        return 1
    if parsed > MAX_TREND_WINDOW:
        return MAX_TREND_WINDOW
    return parsed


def _parse_bool_flag(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_artifact_max_bytes(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_ARTIFACT_READ_MAX_BYTES
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise GuiApiError("'max_bytes' must be an integer") from exc
    if parsed < 1:
        raise GuiApiError("'max_bytes' must be greater than zero")
    if parsed > MAX_ARTIFACT_READ_MAX_BYTES:
        return MAX_ARTIFACT_READ_MAX_BYTES
    return parsed


def _quality_trends(runs_root: Path, window: int, *, allow_partial: bool = False) -> dict[str, Any]:
    trend_window = max(1, min(int(window), MAX_TREND_WINDOW))
    if not runs_root.exists():
        return {
            "window": {"requested": int(window), "applied": trend_window},
            "mode": {"allow_partial": bool(allow_partial)},
            "counters": {
                "runs_total": 0,
                "runs_windowed": 0,
                "runs_included": 0,
                "runs_included_full": 0,
                "runs_included_partial": 0,
                "runs_included_partial_missing_quality": 0,
                "runs_included_partial_missing_validation": 0,
                "runs_skipped_missing_quality": 0,
                "runs_skipped_invalid_quality": 0,
                "runs_skipped_missing_validation": 0,
                "runs_skipped_invalid_validation": 0,
                "runs_skipped_missing_or_invalid_artifacts": 0,
            },
            "runs": [],
            "aggregates": {
                "validators": {"totals": {"total": 0, "passed": 0, "failed": 0, "soft_fail": 0, "skipped": 0, "blocking_failed": 0}, "by_name": {}},
                "blockers": {"total": 0, "unique": 0, "by_name": {}},
            },
        }

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    windowed = run_dirs[:trend_window]

    counters = {
        "runs_total": len(run_dirs),
        "runs_windowed": len(windowed),
        "runs_included": 0,
        "runs_included_full": 0,
        "runs_included_partial": 0,
        "runs_included_partial_missing_quality": 0,
        "runs_included_partial_missing_validation": 0,
        "runs_skipped_missing_quality": 0,
        "runs_skipped_invalid_quality": 0,
        "runs_skipped_missing_validation": 0,
        "runs_skipped_invalid_validation": 0,
        "runs_skipped_missing_or_invalid_artifacts": 0,
    }

    validator_totals = {"total": 0, "passed": 0, "failed": 0, "soft_fail": 0, "skipped": 0, "blocking_failed": 0}
    validator_by_name: dict[str, dict[str, int]] = {}
    blocker_by_name: dict[str, int] = {}
    run_rows: list[dict[str, Any]] = []

    for run_dir in windowed:
        quality_raw, quality_error = _load_json(run_dir / ".autodev" / "task_quality_index.json")
        validation_raw, validation_error = _load_json(run_dir / ".autodev" / "task_final_last_validation.json")

        quality_missing = quality_raw is None and quality_error is None
        validation_missing = validation_raw is None and validation_error is None
        quality_invalid = quality_error is not None
        validation_invalid = validation_error is not None

        if quality_missing:
            counters["runs_skipped_missing_quality"] += 1
        if quality_invalid:
            counters["runs_skipped_invalid_quality"] += 1
        if validation_missing:
            counters["runs_skipped_missing_validation"] += 1
        if validation_invalid:
            counters["runs_skipped_invalid_validation"] += 1

        quality_available = not quality_missing and not quality_invalid
        validation_available = not validation_missing and not validation_invalid
        include_partial = (
            allow_partial
            and (quality_missing ^ validation_missing)
            and not quality_invalid
            and not validation_invalid
        )

        if not (quality_available and validation_available) and not include_partial:
            counters["runs_skipped_missing_or_invalid_artifacts"] += 1
            continue

        quality = quality_raw if isinstance(quality_raw, dict) else {}
        validation = validation_raw if isinstance(validation_raw, dict) else {}

        if validation_available:
            validation_norm = normalize_validation(validation, quality)
            summary = validation_norm.get("summary") if isinstance(validation_norm, dict) else {}
            cards = validation_norm.get("validator_cards") if isinstance(validation_norm, dict) else []
        else:
            summary = {}
            cards = []

        run_validator_counts = {
            "total": int(summary.get("total", 0)) if isinstance(summary, dict) else 0,
            "passed": int(summary.get("passed", 0)) if isinstance(summary, dict) else 0,
            "failed": int(summary.get("failed", 0)) if isinstance(summary, dict) else 0,
            "soft_fail": int(summary.get("soft_fail", 0)) if isinstance(summary, dict) else 0,
            "skipped": int(summary.get("skipped", 0)) if isinstance(summary, dict) else 0,
            "blocking_failed": int(summary.get("blocking_failed", 0)) if isinstance(summary, dict) else 0,
        }

        for key, value in run_validator_counts.items():
            validator_totals[key] += value

        if isinstance(cards, list):
            for row in cards:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("validator") or "unknown").strip() or "unknown"
                status = str(row.get("status") or "unknown").strip().lower()
                bucket = validator_by_name.setdefault(
                    name,
                    {"total": 0, "passed": 0, "failed": 0, "soft_fail": 0, "skipped": 0, "blocking_failed": 0},
                )
                bucket["total"] += 1
                if status == "passed":
                    bucket["passed"] += 1
                elif status == "failed":
                    bucket["failed"] += 1
                elif status == "soft_fail":
                    bucket["soft_fail"] += 1
                elif status == "skipped_dependency":
                    bucket["skipped"] += 1
                else:
                    if row.get("ok") is True:
                        bucket["passed"] += 1
                    elif row.get("ok") is False:
                        bucket["failed"] += 1
                if status in {"failed", "soft_fail"}:
                    bucket["blocking_failed"] += 1

        blockers_raw = quality.get("unresolved_blockers") if quality_available and isinstance(quality.get("unresolved_blockers"), list) else []
        blockers = [str(b) for b in blockers_raw]
        for blocker in blockers:
            blocker_by_name[blocker] = blocker_by_name.get(blocker, 0) + 1

        counters["runs_included"] += 1
        if quality_available and validation_available:
            counters["runs_included_full"] += 1
            inclusion_mode = "full"
        else:
            counters["runs_included_partial"] += 1
            inclusion_mode = "partial"
            if not quality_available:
                counters["runs_included_partial_missing_quality"] += 1
            if not validation_available:
                counters["runs_included_partial_missing_validation"] += 1

        run_rows.append(
            {
                "run_id": run_dir.name,
                "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
                "status": _run_status(quality) if quality_available else "unknown",
                "inclusion_mode": inclusion_mode,
                "artifact_availability": {
                    "quality": quality_available,
                    "validation": validation_available,
                },
                "validator": run_validator_counts,
                "blockers": {"count": len(blockers), "names": blockers},
            }
        )

    return {
        "window": {"requested": int(window), "applied": trend_window},
        "mode": {"allow_partial": bool(allow_partial)},
        "counters": counters,
        "runs": run_rows,
        "aggregates": {
            "validators": {
                "totals": validator_totals,
                "by_name": dict(sorted(validator_by_name.items(), key=lambda item: item[0])),
            },
            "blockers": {
                "total": sum(blocker_by_name.values()),
                "unique": len(blocker_by_name),
                "by_name": dict(sorted(blocker_by_name.items(), key=lambda item: item[0])),
            },
        },
    }


class GuiRequestHandler(BaseHTTPRequestHandler):
    server_version = "AutoDevGuiMvp/0.1"

    @property
    def config(self) -> GuiConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/gui/context":
            self._json_response(
                {
                    "mode": "local_simple" if self.config.local_simple_mode else "hardened",
                    "local_simple_mode": self.config.local_simple_mode,
                    "defaults": {
                        "profile": self.config.default_profile,
                        "out": str(self.config.runs_root),
                        "config": self.config.default_config_path,
                        "prd": self.config.default_prd_path,
                    },
                    "roles": {
                        "read_only": READ_ONLY_ROLE,
                        "mutating": sorted(MUTATING_ROLES),
                    },
                    "api": {
                        "run_controls": ["start", "resume", "stop", "retry"],
                        "trends": True,
                    },
                }
            )
            return

        if path == "/api/processes":
            query = parse_qs(parsed.query)
            limit_raw = str((query.get("limit") or ["100"])[0]).strip()
            state = str((query.get("state") or [""])[0]).strip() or None
            run_id = str((query.get("run_id") or [""])[0]).strip() or None
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 100
            self._json_response(list_processes(limit=limit, state=state, run_id=run_id))
            return

        if path.startswith("/api/processes/") and path.endswith("/history"):
            process_id = unquote(path.removeprefix("/api/processes/").removesuffix("/history")).strip("/")
            if not process_id:
                self._json_response({"error": "process not found"}, status=HTTPStatus.NOT_FOUND)
                return
            try:
                self._json_response(get_process_history(process_id))
            except FileNotFoundError as exc:
                self._json_response({"error": "process not found", "detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return

        if path.startswith("/api/processes/"):
            process_id = unquote(path.removeprefix("/api/processes/")).strip()
            if not process_id:
                self._json_response({"error": "process not found"}, status=HTTPStatus.NOT_FOUND)
                return
            try:
                self._json_response(get_process_detail(process_id))
            except FileNotFoundError as exc:
                self._json_response({"error": "process not found", "detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return

        if path == "/api/runs":
            self._json_response({"runs": _list_runs(self.config.runs_root)})
            return

        if path == "/api/runs/compare":
            query = parse_qs(parsed.query)
            left = str((query.get("left") or query.get("run_a") or [""])[0])
            right = str((query.get("right") or query.get("run_b") or [""])[0])
            payload, status = _run_compare(self.config.runs_root, left, right)
            self._json_response(payload, status=status)
            return

        if path == "/api/runs/trends":
            query = parse_qs(parsed.query)
            window = _parse_trend_window(str((query.get("window") or [""])[0]))
            allow_partial = _parse_bool_flag(str((query.get("partial") or query.get("allow_partial") or [""])[0]))
            self._json_response(_quality_trends(self.config.runs_root, window, allow_partial=allow_partial))
            return

        if path.startswith("/api/runs/") and path.endswith("/artifacts/read"):
            run_id = unquote(path.removeprefix("/api/runs/").removesuffix("/artifacts/read")).strip("/")
            if not run_id:
                self._json_response(
                    {"error": _error_payload("invalid_run_id", "run id is required")},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            query = parse_qs(parsed.query)
            artifact_path = str((query.get("path") or [""])[0]).strip()
            if not artifact_path:
                self._json_response(
                    {"error": _error_payload("missing_artifact_path", "query param 'path' is required")},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                max_bytes = _parse_artifact_max_bytes(str((query.get("max_bytes") or [""])[0]))
                payload = read_artifact(
                    str(self.config.runs_root),
                    run_id,
                    artifact_path,
                    max_bytes=max_bytes,
                )
            except GuiApiError as exc:
                self._json_response(
                    {"error": _error_payload("invalid_artifact_request", str(exc))},
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
                return
            except FileNotFoundError as exc:
                self._json_response(
                    {"error": _error_payload("artifact_not_found", str(exc), run_id=run_id, path=artifact_path)},
                    status=HTTPStatus.NOT_FOUND,
                )
                return

            self._json_response(payload)
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
        if path == "/api/runs/stop":
            self._handle_run_control(action="stop")
            return
        if path == "/api/runs/retry":
            self._handle_run_control(action="retry")
            return
        self._json_response({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_run_control(self, *, action: str) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return

        auth = _resolve_request_auth(headers=self.headers, payload=payload, action=action)
        role = auth.role
        execute = bool(payload.get("execute", False))
        correlation_id = _coerce_correlation_id_for_audit(payload.get("correlation_id"))
        event = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "action": action,
            "role": role,
            "auth": {
                "source": auth.source,
                "subject": auth.subject,
                "scope": auth.scope,
                "policy_name": auth.policy_name,
                "policy_allowed_roles": auth.policy_allowed_roles,
            },
            "payload": _audit_payload_summary(payload, execute=execute),
        }
        if correlation_id:
            event["correlation_id"] = correlation_id

        if not _is_mutation_allowed(role):
            event["result_status"] = "forbidden"
            event["error"] = f"role '{role}' is not allowed to call mutating endpoints"
            error_payload = _error_payload(
                "forbidden_role",
                f"Role '{role}' cannot perform '{action}'.",
                role=role,
                allowed_roles=sorted(MUTATING_ROLES),
                auth_source=auth.source,
                project_scope=(auth.scope or {}).get("project", ""),
                environment_scope=(auth.scope or {}).get("environment", ""),
                policy_allowed_roles=auth.policy_allowed_roles,
            )
            error_payload["fix_hints"] = build_run_control_fix_hints(
                action=action,
                error=error_payload,
                payload=payload,
                runs_root=self.config.runs_root,
            )
            self._audit_then_respond(
                body={"error": error_payload},
                status=HTTPStatus.FORBIDDEN,
                audit_event=event,
            )
            return

        validation_error = _validate_run_control_payload(payload, action=action)
        if validation_error:
            event["result_status"] = "invalid_request"
            event["error"] = validation_error["message"]
            validation_error["fix_hints"] = build_run_control_fix_hints(
                action=action,
                error=validation_error,
                payload=payload,
                runs_root=self.config.runs_root,
            )
            self._audit_then_respond(
                body={"error": validation_error},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                audit_event=event,
            )
            return

        graceful_timeout_sec = float(payload.get("graceful_timeout_sec", 2.0))
        payload.pop("execute", None)

        try:
            if action == "resume":
                resume_info = validate_resume_target(str(payload.get("out", "")))
                result = trigger_resume(payload, execute=execute)
                result["resume_target"] = resume_info
            elif action == "stop":
                result = trigger_stop(payload, graceful_timeout_sec=graceful_timeout_sec)
            elif action == "retry":
                result = trigger_retry(payload, execute=execute)
            else:
                result = trigger_start(payload, execute=execute)
        except GuiApiError as exc:
            event["result_status"] = "invalid_request"
            event["error"] = str(exc)
            error_payload = _error_payload("invalid_payload", str(exc))
            error_payload["fix_hints"] = build_run_control_fix_hints(
                action=action,
                error=error_payload,
                payload=payload,
                runs_root=self.config.runs_root,
            )
            self._audit_then_respond(
                body={"error": error_payload},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                audit_event=event,
            )
            return
        except FileNotFoundError as exc:
            event["result_status"] = "not_found"
            event["error"] = str(exc)
            error_payload = _error_payload("not_found", str(exc))
            error_payload["fix_hints"] = build_run_control_fix_hints(
                action=action,
                error=error_payload,
                payload=payload,
                runs_root=self.config.runs_root,
            )
            self._audit_then_respond(
                body={"error": error_payload},
                status=HTTPStatus.NOT_FOUND,
                audit_event=event,
            )
            return
        except OSError as exc:
            event["result_status"] = "launch_failed"
            event["error"] = str(exc)
            error_payload = _error_payload("launch_failed", f"failed to launch autodev: {exc}")
            error_payload["fix_hints"] = build_run_control_fix_hints(
                action=action,
                error=error_payload,
                payload=payload,
                runs_root=self.config.runs_root,
            )
            self._audit_then_respond(
                body={"error": error_payload},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                audit_event=event,
            )
            return

        result_correlation_id = str(result.get("correlation_id") or "").strip()
        if result_correlation_id:
            event["correlation_id"] = result_correlation_id

        if action == "stop":
            event["result_status"] = "stopped"
        else:
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

    correlation_id = payload.get("correlation_id")
    if correlation_id is not None:
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            return _error_payload("invalid_correlation_id", "'correlation_id' must be a non-empty string")
        if not _is_supported_run_token(correlation_id.strip()):
            return _error_payload(
                "invalid_correlation_id",
                "'correlation_id' contains unsupported characters. Allowed: letters, digits, dot, underscore, slash, colon, at, plus, hyphen.",
                field="correlation_id",
            )

    if action in {"stop", "retry"}:
        process_id = payload.get("process_id")
        run_id = payload.get("run_id")
        if action == "stop":
            if not isinstance(process_id, str) or not process_id.strip():
                return _error_payload("missing_process_id", "'process_id' is required")
            timeout_val = payload.get("graceful_timeout_sec", 2.0)
            if not isinstance(timeout_val, (int, float)):
                return _error_payload("invalid_graceful_timeout", "'graceful_timeout_sec' must be a number")
            if float(timeout_val) <= 0:
                return _error_payload(
                    "invalid_graceful_timeout", "'graceful_timeout_sec' must be greater than zero"
                )
            return None

        process_id_ok = isinstance(process_id, str) and bool(process_id.strip())
        run_id_ok = isinstance(run_id, str) and bool(run_id.strip())
        if not process_id_ok and not run_id_ok:
            return _error_payload("missing_retry_target", "'process_id' or 'run_id' is required")
        return None

    prd = payload.get("prd")
    if not isinstance(prd, str):
        return _error_payload("missing_prd", "'prd' is required", field="prd")
    prd_value = prd.strip()
    if not prd_value:
        return _error_payload("missing_prd", "'prd' is required", field="prd")
    if _contains_unsafe_path_chars(prd_value):
        return _error_payload(
            "invalid_prd",
            "'prd' contains unsafe characters (newline/NUL are not allowed)",
            field="prd",
        )

    prd_path = Path(prd_value).expanduser()
    if not prd_path.is_file():
        return _error_payload("invalid_prd", "'prd' must point to an existing file", field="prd")

    out = payload.get("out")
    if not isinstance(out, str):
        return _error_payload("missing_out", "'out' is required", field="out")
    out_value = out.strip()
    if not out_value:
        return _error_payload("missing_out", "'out' is required", field="out")
    if _contains_unsafe_path_chars(out_value):
        return _error_payload(
            "invalid_out",
            "'out' contains unsafe characters (newline/NUL are not allowed)",
            field="out",
        )
    out_path = Path(out_value).expanduser()

    if out_path.exists() and not out_path.is_dir():
        return _error_payload("invalid_out", "'out' must be a directory path", field="out")

    if action == "resume" and not out_path.exists():
        return _error_payload(
            "resume_out_missing",
            "'out' must point to an existing run directory for resume",
            field="out",
        )

    profile = payload.get("profile")
    if not isinstance(profile, str):
        return _error_payload("missing_profile", "'profile' is required", field="profile")
    profile_value = profile.strip()
    if not profile_value:
        return _error_payload("missing_profile", "'profile' is required", field="profile")
    if not _is_supported_run_token(profile_value):
        return _error_payload(
            "invalid_profile",
            "'profile' contains unsupported characters. Allowed: letters, digits, dot, underscore, slash, colon, at, plus, hyphen.",
            field="profile",
        )

    model = payload.get("model")
    if model is not None:
        if not isinstance(model, str):
            return _error_payload("invalid_model", "'model' must be a string", field="model")
        model_value = model.strip()
        if model_value and not _is_supported_run_token(model_value):
            return _error_payload(
                "invalid_model",
                "'model' contains unsupported characters. Allowed: letters, digits, dot, underscore, slash, colon, at, plus, hyphen.",
                field="model",
            )

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
    local_simple_mode = _is_local_simple_mode()
    default_profile = str(os.environ.get(DEFAULT_PROFILE_ENV, "")).strip() or (
        "local_simple" if local_simple_mode else "enterprise"
    )
    default_config_path = str(os.environ.get(DEFAULT_CONFIG_ENV, "")).strip()
    default_prd_path = str(os.environ.get(DEFAULT_PRD_ENV, "")).strip()
    config = GuiConfig(
        runs_root=runs_root.resolve(),
        static_root=static_root,
        local_simple_mode=local_simple_mode,
        default_profile=default_profile,
        default_config_path=default_config_path,
        default_prd_path=default_prd_path,
    )

    httpd = ThreadingHTTPServer((host, port), GuiRequestHandler)
    httpd.config = config  # type: ignore[attr-defined]

    print(f"[gui-mvp] serving http://{host}:{port}")
    print(f"[gui-mvp] runs root: {config.runs_root}")
    print(f"[gui-mvp] mode: {'local_simple' if config.local_simple_mode else 'hardened'}")
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
