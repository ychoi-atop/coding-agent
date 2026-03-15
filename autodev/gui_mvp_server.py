from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from .autonomous_mode import (
    AUTONOMOUS_REPORT_JSON,
    build_operator_audit_summary,
    extract_autonomous_summary,
)
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
from .trust_intelligence import build_trust_intelligence_packet, build_trust_summary


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
SAFE_COMPARE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
COMPARE_SNAPSHOT_DIR = ".autodev/compare_snapshots"
COMPARE_SNAPSHOT_RECORD_VERSION = "compare-trust-snapshot-record-v1"
DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE = 20
MAX_COMPARE_SNAPSHOT_PAGE_SIZE = 100
COMPARE_SNAPSHOT_SORTS = {"newest", "oldest", "name", "baseline", "candidate"}

SCORECARD_CARD_ORDER: list[tuple[str, str]] = [
    ("task_pass_rate_percent", "Pass Rate"),
    ("task_pass_fraction", "Tasks"),
    ("total_task_attempts", "Attempts"),
    ("repair_passes", "Repairs"),
    ("hard_failures", "Hard Fails"),
    ("soft_failures", "Soft Fails"),
]


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


def _load_json(
    path: Path,
    *,
    expected_type: type[Any] | tuple[type[Any], ...] | None = None,
    artifact_name: str = "",
) -> tuple[Any | None, dict[str, Any] | None]:
    if not path.exists():
        return None, None

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
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

    if expected_type is not None and not isinstance(parsed, expected_type):
        expected_label = _expected_type_label(expected_type)
        artifact_label = artifact_name or path.name
        return None, {
            "kind": "artifact_json_error",
            "code": "artifact_json_type_mismatch",
            "path": str(path),
            "artifact": artifact_label,
            "expected": expected_label,
            "actual": type(parsed).__name__,
            "message": f"expected JSON {expected_label} for {artifact_label}",
        }

    return parsed, None


def _run_status(quality: dict[str, Any] | None) -> str:
    return normalize_run_status(quality_index=quality, default="unknown")


def _list_runs(runs_root: Path) -> list[dict[str, Any]]:
    if not runs_root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for d in sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        quality, quality_error = _load_json(
            d / ".autodev" / "task_quality_index.json",
            expected_type=dict,
            artifact_name="task_quality_index",
        )
        run_trace, run_trace_error = _load_json(
            d / ".autodev" / "run_trace.json",
            expected_type=dict,
            artifact_name="run_trace",
        )
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
    quality, quality_error = _load_json(
        run_dir / ".autodev" / "task_quality_index.json",
        expected_type=dict,
        artifact_name="task_quality_index",
    )
    final_validation, validation_error = _load_json(
        run_dir / ".autodev" / "task_final_last_validation.json",
        expected_type=dict,
        artifact_name="task_final_last_validation",
    )
    run_trace, run_trace_error = _load_json(
        run_dir / ".autodev" / "run_trace.json",
        expected_type=dict,
        artifact_name="run_trace",
    )
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
    trust_summary = None
    trust_packet = None
    trust_message = "Trust intelligence is not available for this run."
    if (run_dir / AUTONOMOUS_REPORT_JSON).exists():
        autonomous_snapshot = extract_autonomous_summary(str(run_dir))
        trust_packet = build_trust_intelligence_packet(run_dir, summary=autonomous_snapshot)
        trust_summary = build_trust_summary(trust_packet)
        trust_message = ""
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
        "trust_summary": trust_summary,
        "trust_packet": trust_packet,
        "trust_message": trust_message,
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
    left_trust = left_summary.get("trust") if isinstance(left_summary.get("trust"), dict) else {}
    right_trust = right_summary.get("trust") if isinstance(right_summary.get("trust"), dict) else {}

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
            "trust_score": _safe_int(round((float(right_trust.get("score") or 0.0) - float(left_trust.get("score") or 0.0)) * 100), default=0) / 100,
            "trust_status_changed": left_trust.get("status") != right_trust.get("status"),
            "trust_review_changed": left_trust.get("requires_human_review") != right_trust.get("requires_human_review"),
            "trust_quality_status_changed": left_trust.get("latest_quality_status") != right_trust.get("latest_quality_status"),
            "trust_owner_changed": left_trust.get("incident_owner_team") != right_trust.get("incident_owner_team"),
            "trust_severity_changed": left_trust.get("incident_severity") != right_trust.get("incident_severity"),
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _expected_type_label(expected_type: type[Any] | tuple[type[Any], ...]) -> str:
    if isinstance(expected_type, tuple):
        labels = [tp.__name__ for tp in expected_type]
        return " or ".join(labels)
    return expected_type.__name__


def _derive_scorecard_from_quality(quality_index: dict[str, Any]) -> dict[str, Any]:
    tasks = quality_index.get("tasks") if isinstance(quality_index.get("tasks"), list) else []
    final = quality_index.get("final") if isinstance(quality_index.get("final"), dict) else {}
    totals = quality_index.get("totals") if isinstance(quality_index.get("totals"), dict) else {}

    passed = sum(1 for row in tasks if isinstance(row, dict) and str(row.get("status") or "").lower() == "passed")
    total = len(tasks)
    pass_rate = round((passed / total * 100.0), 1) if total else 0.0

    return {
        "task_pass_rate_percent": pass_rate,
        "task_pass_count": passed,
        "task_total": total,
        "task_pass_fraction": f"{passed}/{total}",
        "final_status": str(final.get("status") or "unknown").strip() or "unknown",
        "total_task_attempts": _safe_int(totals.get("total_task_attempts"), 0),
        "hard_failures": _safe_int(totals.get("hard_failures"), 0),
        "soft_failures": _safe_int(totals.get("soft_failures"), 0),
        "repair_passes": _safe_int(totals.get("repair_passes"), 0),
        "unresolved_blocker_count": len(quality_index.get("unresolved_blockers", []))
        if isinstance(quality_index.get("unresolved_blockers"), list)
        else 0,
    }


def _scorecard_card_tone(key: str, value: Any) -> str:
    if key == "hard_failures":
        return "danger" if _safe_int(value, 0) > 0 else "ok"
    if key == "soft_failures":
        return "warning" if _safe_int(value, 0) > 0 else "ok"
    if key == "task_pass_rate_percent":
        return "ok" if float(value or 0) >= 100.0 else "neutral"
    return "neutral"


def _build_scorecard_cards(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in SCORECARD_CARD_ORDER:
        value = scorecard.get(key)
        display = f"{value}%" if key == "task_pass_rate_percent" else str(value)
        rows.append(
            {
                "key": key,
                "label": label,
                "value": display,
                "tone": _scorecard_card_tone(key, value),
            }
        )
    return rows


def _latest_scorecard_summary(runs_root: Path) -> dict[str, Any]:
    if not runs_root.exists() or not runs_root.is_dir():
        return {
            "empty": True,
            "message": "No runs root found.",
            "latest": None,
            "summary": None,
            "cards": [],
            "artifact_errors": [],
        }

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return {
            "empty": True,
            "message": "No runs found in runs root.",
            "latest": None,
            "summary": None,
            "cards": [],
            "artifact_errors": [],
        }

    run_dir = run_dirs[0]
    quality_raw, quality_error = _load_json(
        run_dir / ".autodev" / "task_quality_index.json",
        expected_type=dict,
        artifact_name="task_quality_index",
    )
    run_trace_raw, run_trace_error = _load_json(
        run_dir / ".autodev" / "run_trace.json",
        expected_type=dict,
        artifact_name="run_trace",
    )
    metadata_raw, metadata_error = _load_json(
        run_dir / ".autodev" / "run_metadata.json",
        expected_type=dict,
        artifact_name="run_metadata",
    )
    checkpoint_raw, checkpoint_error = _load_json(
        run_dir / ".autodev" / "checkpoint.json",
        expected_type=dict,
        artifact_name="checkpoint",
    )

    quality = quality_raw if isinstance(quality_raw, dict) else {}
    run_trace = run_trace_raw if isinstance(run_trace_raw, dict) else {}
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    checkpoint = checkpoint_raw if isinstance(checkpoint_raw, dict) else {}

    profile = ""
    resolved_profile = quality.get("resolved_quality_profile")
    if isinstance(resolved_profile, dict):
        profile = str(resolved_profile.get("name") or "").strip()
    if not profile:
        profile = str(metadata.get("requested_profile") or "").strip()

    trace_dto = normalize_run_trace(run_trace)
    scorecard = _derive_scorecard_from_quality(quality)
    artifact_errors = [err for err in [quality_error, run_trace_error, metadata_error, checkpoint_error] if err]

    return {
        "empty": False,
        "message": "",
        "latest": {
            "run_id": run_dir.name,
            "path": str(run_dir),
            "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
            "status": normalize_run_status(metadata=metadata, checkpoint=checkpoint, quality_index=quality, default="unknown"),
            "profile": profile,
            "model": str(trace_dto.get("model") or "").strip(),
            "started_at": str(trace_dto.get("started_at") or "").strip(),
            "completed_at": str(trace_dto.get("completed_at") or "").strip(),
        },
        "summary": scorecard,
        "cards": _build_scorecard_cards(scorecard),
        "artifact_errors": artifact_errors,
    }


def _latest_quality_gate_snapshot(runs_root: Path) -> dict[str, Any]:
    if not runs_root.exists() or not runs_root.is_dir():
        return {
            "empty": True,
            "message": "No runs root found.",
            "latest": None,
            "summary": None,
            "snapshot": None,
            "warnings": [],
        }

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return {
            "empty": True,
            "message": "No runs found in runs root.",
            "latest": None,
            "summary": None,
            "snapshot": None,
            "warnings": [],
        }

    run_dir = run_dirs[0]
    snapshot = extract_autonomous_summary(str(run_dir))
    latest_run = snapshot.get("latest_run") if isinstance(snapshot.get("latest_run"), dict) else {}

    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []

    summary = build_operator_audit_summary(snapshot)
    trust_packet = build_trust_intelligence_packet(run_dir, summary=snapshot)
    trust_summary = build_trust_summary(trust_packet)

    return {
        "empty": False,
        "message": "",
        "latest": {
            "run_id": str(latest_run.get("run_id") or run_dir.name),
            "path": str(run_dir),
            "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
            "profile": latest_run.get("profile"),
            "request_id": latest_run.get("request_id"),
            "completed_at": latest_run.get("completed_at"),
        },
        "summary": summary,
        "trust": trust_summary,
        "snapshot": snapshot,
        "warnings": [str(item) for item in warnings if item],
    }


def _latest_trust_snapshot(runs_root: Path) -> dict[str, Any]:
    if not runs_root.exists() or not runs_root.is_dir():
        return {
            "empty": True,
            "message": "No runs root found.",
            "latest": None,
            "summary": None,
            "packet": None,
            "warnings": [],
        }

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return {
            "empty": True,
            "message": "No runs found in runs root.",
            "latest": None,
            "summary": None,
            "packet": None,
            "warnings": [],
        }

    run_dir = run_dirs[0]
    snapshot = extract_autonomous_summary(str(run_dir))
    latest_run = snapshot.get("latest_run") if isinstance(snapshot.get("latest_run"), dict) else {}
    warnings = snapshot.get("warnings") if isinstance(snapshot.get("warnings"), list) else []
    packet = build_trust_intelligence_packet(run_dir, summary=snapshot)
    summary = build_trust_summary(packet)

    return {
        "empty": False,
        "message": "",
        "latest": {
            "run_id": str(latest_run.get("run_id") or run_dir.name),
            "path": str(run_dir),
            "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
            "profile": latest_run.get("profile"),
            "request_id": latest_run.get("request_id"),
            "completed_at": latest_run.get("completed_at"),
        },
        "summary": summary,
        "packet": packet,
        "warnings": [str(item) for item in warnings if item],
    }


def _trust_trends(runs_root: Path, window: int) -> dict[str, Any]:
    trend_window = max(1, min(int(window), MAX_TREND_WINDOW))
    if not runs_root.exists() or not runs_root.is_dir():
        return {
            "window": {"requested": int(window), "applied": trend_window},
            "empty": True,
            "message": "No runs root found.",
            "summary": {
                "runs_considered": 0,
                "avg_trust_score": 0.0,
                "review_required_count": 0,
                "status_counts": {"high": 0, "moderate": 0, "low": 0, "unknown": 0},
                "trend_direction": "flat",
                "score_delta": 0.0,
            },
            "runs": [],
            "warnings": [],
        }

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    windowed = run_dirs[:trend_window]
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    status_counts = {"high": 0, "moderate": 0, "low": 0, "unknown": 0}
    score_total = 0.0
    review_required_count = 0

    for run_dir in windowed:
        try:
            snapshot = extract_autonomous_summary(str(run_dir))
            packet = build_trust_intelligence_packet(run_dir, summary=snapshot)
            summary = build_trust_summary(packet)
        except Exception as exc:
            warnings.append(f"{run_dir.name}: {exc}")
            continue

        trust_status = str(summary.get("trust_status") or "unknown").lower()
        if trust_status not in status_counts:
            trust_status = "unknown"
        status_counts[trust_status] += 1
        trust_score = float(summary.get("trust_score") or 0.0)
        score_total += trust_score
        if summary.get("requires_human_review") is True:
            review_required_count += 1

        latest_run = snapshot.get("latest_run") if isinstance(snapshot.get("latest_run"), dict) else {}
        rows.append(
            {
                "run_id": str(latest_run.get("run_id") or run_dir.name),
                "path": str(run_dir),
                "completed_at": latest_run.get("completed_at"),
                "updated_at": datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(),
                "profile": latest_run.get("profile"),
                "status": summary.get("status"),
                "trust_status": trust_status,
                "trust_score": round(trust_score, 2),
                "requires_human_review": bool(summary.get("requires_human_review")),
                "human_review_reasons": summary.get("human_review_reasons") if isinstance(summary.get("human_review_reasons"), list) else [],
                "latest_quality_status": summary.get("latest_quality_status"),
                "latest_quality_score": summary.get("latest_quality_score"),
                "incident_owner_team": summary.get("incident_owner_team"),
                "incident_severity": summary.get("incident_severity"),
            }
        )

    if not rows:
        return {
            "window": {"requested": int(window), "applied": trend_window},
            "empty": True,
            "message": "No trust trend data available yet.",
            "summary": {
                "runs_considered": 0,
                "avg_trust_score": 0.0,
                "review_required_count": 0,
                "status_counts": status_counts,
                "trend_direction": "flat",
                "score_delta": 0.0,
            },
            "runs": [],
            "warnings": warnings,
        }

    newest_score = float(rows[0].get("trust_score") or 0.0)
    oldest_score = float(rows[-1].get("trust_score") or 0.0)
    score_delta = round(newest_score - oldest_score, 2)
    if score_delta > 0.05:
        trend_direction = "improving"
    elif score_delta < -0.05:
        trend_direction = "regressing"
    else:
        trend_direction = "flat"

    return {
        "window": {"requested": int(window), "applied": trend_window},
        "empty": False,
        "message": "",
        "summary": {
            "runs_considered": len(rows),
            "avg_trust_score": round(score_total / len(rows), 2),
            "review_required_count": review_required_count,
            "status_counts": status_counts,
            "trend_direction": trend_direction,
            "score_delta": score_delta,
            "latest_run_id": rows[0].get("run_id"),
        },
        "runs": rows,
        "warnings": warnings,
    }


def _read_experiment_log(run_dir: Path, *, task_id: str | None = None) -> dict[str, Any]:
    """Read and parse experiment log JSONL from a run directory."""
    log_path = run_dir / ".autodev" / "experiment_log.jsonl"
    if not log_path.exists():
        return {"entries": [], "summary": {"entry_count": 0, "tasks": {}}, "run_id": run_dir.name}

    entries: list[dict[str, Any]] = []
    tasks: dict[str, dict[str, Any]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = entry.get("task_id", "")
        if task_id and tid != task_id:
            continue
        entries.append(entry)

        if tid not in tasks:
            tasks[tid] = {"attempts": 0, "decisions": {"accepted": 0, "reverted": 0, "neutral": 0}, "best_score": 0.0, "final_score": 0.0}
        t = tasks[tid]
        t["attempts"] += 1
        decision = entry.get("decision", {}).get("decision", "")
        if decision in t["decisions"]:
            t["decisions"][decision] += 1
        composite = entry.get("composite_score", 0.0)
        t["best_score"] = max(t["best_score"], composite)
        t["final_score"] = composite

    return {
        "entries": entries,
        "summary": {"entry_count": len(entries), "tasks": tasks},
        "run_id": run_dir.name,
    }


def _experiment_log_for_latest_or_run(
    runs_root: Path,
    run_id: str | None = None,
    task_id: str | None = None,
) -> tuple[dict[str, Any], HTTPStatus]:
    """Return experiment log for a specific run or the latest run."""
    if not runs_root.exists() or not runs_root.is_dir():
        return {"entries": [], "summary": {"entry_count": 0, "tasks": {}}, "error": "no runs root"}, HTTPStatus.OK

    if run_id:
        run_dir = runs_root / run_id
        if not run_dir.exists() or not run_dir.is_dir():
            return {"error": "run not found", "run_id": run_id}, HTTPStatus.NOT_FOUND
        return _read_experiment_log(run_dir, task_id=task_id), HTTPStatus.OK

    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    if not run_dirs:
        return {"entries": [], "summary": {"entry_count": 0, "tasks": {}}, "error": "no runs found"}, HTTPStatus.OK
    return _read_experiment_log(run_dirs[0], task_id=task_id), HTTPStatus.OK


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
        quality_raw, quality_error = _load_json(
            run_dir / ".autodev" / "task_quality_index.json",
            expected_type=dict,
            artifact_name="task_quality_index",
        )
        validation_raw, validation_error = _load_json(
            run_dir / ".autodev" / "task_final_last_validation.json",
            expected_type=dict,
            artifact_name="task_final_last_validation",
        )

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

        # Quality score data from experiment log
        exp_log_data = _read_experiment_log(run_dir)
        exp_summary = exp_log_data.get("summary", {})
        exp_tasks = exp_summary.get("tasks", {})
        quality_scores: dict[str, Any] = {}
        if exp_tasks:
            final_scores = [float(t.get("best_score", 0)) for t in exp_tasks.values() if isinstance(t, dict)]
            if final_scores:
                quality_scores = {
                    "composite_avg": round(sum(final_scores) / len(final_scores), 2),
                    "composite_min": round(min(final_scores), 2),
                    "composite_max": round(max(final_scores), 2),
                    "task_count": len(final_scores),
                }

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
                "quality_scores": quality_scores,
            }
        )

    # Quality score aggregates across runs
    composite_avgs = [r["quality_scores"]["composite_avg"] for r in run_rows if r.get("quality_scores")]
    quality_aggregates: dict[str, Any] = {}
    if composite_avgs:
        quality_aggregates = {
            "composite_avg_trend": composite_avgs,
            "composite_avg_mean": round(sum(composite_avgs) / len(composite_avgs), 2),
            "composite_avg_min": round(min(composite_avgs), 2),
            "composite_avg_max": round(max(composite_avgs), 2),
            "runs_with_scores": len(composite_avgs),
        }

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
            "quality": quality_aggregates,
        },
    }


def _compare_snapshots_root(runs_root: Path) -> Path:
    return runs_root / ".autodev" / "compare_snapshots"


def _stable_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _compute_compare_snapshot_integrity(
    snapshot: dict[str, Any],
    compare_payload: dict[str, Any],
    markdown_text: str,
) -> dict[str, str]:
    snapshot_bytes = _stable_json_bytes(snapshot)
    compare_payload_bytes = _stable_json_bytes(compare_payload)
    markdown_bytes = markdown_text.encode("utf-8")
    content_hash = hashlib.sha256()
    content_hash.update(snapshot_bytes)
    content_hash.update(b"\0")
    content_hash.update(compare_payload_bytes)
    content_hash.update(b"\0")
    content_hash.update(markdown_bytes)
    return {
        "snapshot_sha256": _sha256_hex(snapshot_bytes),
        "compare_payload_sha256": _sha256_hex(compare_payload_bytes),
        "markdown_sha256": _sha256_hex(markdown_bytes),
        "content_sha256": content_hash.hexdigest(),
    }


def _sanitize_compare_snapshot_segment(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text or fallback


def _normalize_compare_snapshot_tags(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(part).strip() for part in raw]

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized[:40])
        if len(out) >= 12:
            break
    return out


def _compare_snapshot_integrity_status(record: dict[str, Any]) -> dict[str, Any]:
    snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), dict) else {}
    compare_payload = record.get("compare_payload") if isinstance(record.get("compare_payload"), dict) else {}
    markdown_text = str(record.get("markdown") or "")
    computed = _compute_compare_snapshot_integrity(snapshot, compare_payload, markdown_text)
    stored = record.get("integrity") if isinstance(record.get("integrity"), dict) else {}
    mismatches: list[str] = []
    for key, expected in computed.items():
        actual = str(stored.get(key) or "").strip()
        if actual and actual != expected:
            mismatches.append(key)
    return {
        "ok": not mismatches,
        "stored": stored,
        "computed": computed,
        "mismatches": mismatches,
    }


def _compare_snapshot_metadata(
    record: dict[str, Any],
    json_path: Path,
    *,
    integrity: dict[str, Any] | None = None,
    duplicate_of: str = "",
    duplicate_count: int = 1,
) -> dict[str, Any]:
    snapshot = record.get("snapshot") if isinstance(record.get("snapshot"), dict) else {}
    left = snapshot.get("left") if isinstance(snapshot.get("left"), dict) else {}
    right = snapshot.get("right") if isinstance(snapshot.get("right"), dict) else {}
    md_path = json_path.with_suffix(".md")
    left_run_id = str(left.get("run_id") or "")
    right_run_id = str(right.get("run_id") or "")
    default_name = f"{left_run_id or 'baseline'} vs {right_run_id or 'candidate'}"
    integrity_status = integrity if integrity is not None else _compare_snapshot_integrity_status(record)
    archived = bool(record.get("archived", False))
    return {
        "snapshot_id": str(record.get("snapshot_id") or json_path.stem),
        "schema_version": str(record.get("schema_version") or COMPARE_SNAPSHOT_RECORD_VERSION),
        "persisted_at": str(record.get("persisted_at") or ""),
        "generated_at": str(snapshot.get("generated_at") or ""),
        "source": str(snapshot.get("source") or ""),
        "display_name": str(record.get("display_name") or default_name),
        "pinned": bool(record.get("pinned", False)),
        "archived": archived,
        "archived_at": str(record.get("archived_at") or ""),
        "tags": _normalize_compare_snapshot_tags(record.get("tags")),
        "left_run_id": left_run_id,
        "right_run_id": right_run_id,
        "integrity_ok": bool(integrity_status.get("ok", True)),
        "integrity_mismatches": list(integrity_status.get("mismatches") or []),
        "content_sha256": str((integrity_status.get("computed") or {}).get("content_sha256") or ""),
        "duplicate_of": duplicate_of,
        "duplicate_count": max(1, int(duplicate_count or 1)),
        "json_path": str(json_path),
        "markdown_path": str(md_path) if md_path.exists() else "",
    }


def _load_compare_snapshot_record(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("compare snapshot record must be a JSON object")
    return payload


def _read_compare_snapshot_entries(runs_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    root = _compare_snapshots_root(runs_root)
    if not root.exists():
        return [], []

    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            record = _load_compare_snapshot_record(path)
            integrity = _compare_snapshot_integrity_status(record)
            entries.append(
                {
                    "path": path,
                    "record": record,
                    "integrity": integrity,
                }
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return entries, warnings


def _annotate_compare_snapshot_duplicates(entries: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        content_hash = str((entry.get("integrity") or {}).get("computed", {}).get("content_sha256") or "")
        if not content_hash:
            continue
        groups.setdefault(content_hash, []).append(entry)

    for rows in groups.values():
        rows.sort(
            key=lambda row: (
                str((row.get("record") or {}).get("persisted_at") or ""),
                row["path"].stat().st_mtime_ns if isinstance(row.get("path"), Path) and row["path"].exists() else 0,
                str((row.get("record") or {}).get("snapshot_id") or row["path"].stem),
            )
        )
        canonical_id = str((rows[0].get("record") or {}).get("snapshot_id") or rows[0]["path"].stem) if rows else ""
        for row in rows:
            row["duplicate_count"] = len(rows)
            current_id = str((row.get("record") or {}).get("snapshot_id") or row["path"].stem)
            row["duplicate_of"] = canonical_id if len(rows) > 1 and current_id != canonical_id else ""


def _parse_compare_snapshot_page(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return 1
    try:
        parsed = int(raw)
    except ValueError:
        return 1
    return max(1, parsed)


def _parse_compare_snapshot_page_size(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE
    return min(MAX_COMPARE_SNAPSHOT_PAGE_SIZE, max(1, parsed))


def _normalize_compare_snapshot_sort(raw: str | None) -> str:
    normalized = str(raw or "").strip().lower()
    return normalized if normalized in COMPARE_SNAPSHOT_SORTS else "newest"


def _normalize_compare_snapshot_archive_filter(raw: str | None) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized in {"all", "archived"}:
        return normalized
    return "active"


def _normalize_compare_snapshot_pinned_filter(raw: str | None) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized in {"pinned", "unpinned"}:
        return normalized
    return "all"


def _parse_compare_snapshot_filter_datetime(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if len(text) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.fromisoformat(f"{text}T00:00:00+00:00")
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _compare_snapshot_passes_filters(
    metadata: dict[str, Any],
    *,
    query: str,
    archive_filter: str,
    pinned_filter: str,
    baseline_run_id: str,
    candidate_run_id: str,
    tag_filter: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> bool:
    archived = bool(metadata.get("archived", False))
    if archive_filter == "active" and archived:
        return False
    if archive_filter == "archived" and not archived:
        return False

    pinned = bool(metadata.get("pinned", False))
    if pinned_filter == "pinned" and not pinned:
        return False
    if pinned_filter == "unpinned" and pinned:
        return False

    left_run_id = str(metadata.get("left_run_id") or "")
    right_run_id = str(metadata.get("right_run_id") or "")
    if baseline_run_id and baseline_run_id.lower() not in left_run_id.lower():
        return False
    if candidate_run_id and candidate_run_id.lower() not in right_run_id.lower():
        return False

    tags = [str(tag or "") for tag in metadata.get("tags") or []]
    if tag_filter:
        joined_tags = " ".join(tags).lower()
        if tag_filter.lower() not in joined_tags:
            return False

    persisted_at = _parse_compare_snapshot_filter_datetime(str(metadata.get("persisted_at") or ""))
    if date_from and persisted_at and persisted_at < date_from:
        return False
    if date_from and persisted_at is None:
        return False
    if date_to and persisted_at and persisted_at > date_to:
        return False
    if date_to and persisted_at is None:
        return False

    if query:
        haystack = " ".join(
            [
                str(metadata.get("display_name") or ""),
                str(metadata.get("snapshot_id") or ""),
                left_run_id,
                right_run_id,
                str(metadata.get("persisted_at") or ""),
                str(metadata.get("source") or ""),
                "archived" if archived else "active",
                "pinned" if pinned else "unpinned",
                *tags,
            ]
        ).lower()
        if query.lower() not in haystack:
            return False

    return True


def _sort_compare_snapshot_metadata(rows: list[dict[str, Any]], sort: str) -> None:
    if sort == "oldest":
        rows.sort(key=lambda row: (not bool(row.get("pinned", False)), str(row.get("persisted_at") or ""), str(row.get("snapshot_id") or "")))
        return
    if sort == "name":
        rows.sort(key=lambda row: (not bool(row.get("pinned", False)), str(row.get("display_name") or "").lower(), str(row.get("snapshot_id") or "")))
        return
    if sort == "baseline":
        rows.sort(key=lambda row: (not bool(row.get("pinned", False)), str(row.get("left_run_id") or "").lower(), str(row.get("snapshot_id") or "")))
        return
    if sort == "candidate":
        rows.sort(key=lambda row: (not bool(row.get("pinned", False)), str(row.get("right_run_id") or "").lower(), str(row.get("snapshot_id") or "")))
        return

    rows.sort(
        key=lambda row: (
            not bool(row.get("pinned", False)),
            str(row.get("persisted_at") or ""),
            str(row.get("snapshot_id") or ""),
        ),
        reverse=False,
    )
    pinned_rows = [row for row in rows if bool(row.get("pinned", False))]
    unpinned_rows = [row for row in rows if not bool(row.get("pinned", False))]
    pinned_rows.reverse()
    unpinned_rows.reverse()
    rows[:] = pinned_rows + unpinned_rows


def _persist_compare_snapshot(runs_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    compare_payload = payload.get("compare_payload") if isinstance(payload.get("compare_payload"), dict) else {}
    markdown = payload.get("markdown")
    markdown_text = markdown if isinstance(markdown, str) else ""

    if not snapshot:
        raise GuiApiError("field 'snapshot' is required")
    if not compare_payload:
        raise GuiApiError("field 'compare_payload' is required")

    left = snapshot.get("left") if isinstance(snapshot.get("left"), dict) else {}
    right = snapshot.get("right") if isinstance(snapshot.get("right"), dict) else {}
    left_id = _sanitize_compare_snapshot_segment(left.get("run_id"), "baseline")
    right_id = _sanitize_compare_snapshot_segment(right.get("run_id"), "candidate")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{timestamp}__{left_id}__vs__{right_id}__{uuid4().hex[:8]}"
    persisted_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    record = {
        "schema_version": COMPARE_SNAPSHOT_RECORD_VERSION,
        "snapshot_id": snapshot_id,
        "persisted_at": persisted_at,
        "display_name": payload.get("display_name") or f"{left.get('run_id') or 'baseline'} vs {right.get('run_id') or 'candidate'}",
        "pinned": bool(payload.get("pinned", False)),
        "archived": bool(payload.get("archived", False)),
        "archived_at": persisted_at if bool(payload.get("archived", False)) else "",
        "tags": _normalize_compare_snapshot_tags(payload.get("tags")),
        "snapshot": snapshot,
        "compare_payload": compare_payload,
        "markdown": markdown_text,
    }
    record["integrity"] = _compute_compare_snapshot_integrity(snapshot, compare_payload, markdown_text)

    root = _compare_snapshots_root(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / f"{snapshot_id}.json"
    md_path = root / f"{snapshot_id}.md"
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    md_path.write_text(markdown_text, encoding="utf-8")
    entries, _ = _read_compare_snapshot_entries(runs_root)
    _annotate_compare_snapshot_duplicates(entries)
    duplicate_of = ""
    duplicate_count = 1
    for entry in entries:
        current_id = str((entry.get("record") or {}).get("snapshot_id") or "")
        if current_id == snapshot_id:
            duplicate_of = str(entry.get("duplicate_of") or "")
            duplicate_count = max(1, int(entry.get("duplicate_count") or 1))
            break
    return _compare_snapshot_metadata(record, json_path, duplicate_of=duplicate_of, duplicate_count=duplicate_count)


def _list_compare_snapshots(
    runs_root: Path,
    *,
    query: str = "",
    sort: str = "newest",
    archive_filter: str = "active",
    pinned_filter: str = "all",
    baseline_run_id: str = "",
    candidate_run_id: str = "",
    tag_filter: str = "",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE,
) -> dict[str, Any]:
    entries, warnings = _read_compare_snapshot_entries(runs_root)
    _annotate_compare_snapshot_duplicates(entries)

    rows: list[dict[str, Any]] = []
    for entry in entries:
        metadata = _compare_snapshot_metadata(
            entry["record"],
            entry["path"],
            integrity=entry["integrity"],
            duplicate_of=str(entry.get("duplicate_of") or ""),
            duplicate_count=max(1, int(entry.get("duplicate_count") or 1)),
        )
        if _compare_snapshot_passes_filters(
            metadata,
            query=query,
            archive_filter=archive_filter,
            pinned_filter=pinned_filter,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            tag_filter=tag_filter,
            date_from=date_from,
            date_to=date_to,
        ):
            rows.append(metadata)

    _sort_compare_snapshot_metadata(rows, sort)

    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    paged_rows = rows[start : start + page_size]
    return {
        "snapshots": paged_rows,
        "warnings": warnings,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort": sort,
            "query": query,
            "archive_filter": archive_filter,
            "pinned_filter": pinned_filter,
            "baseline_run_id": baseline_run_id,
            "candidate_run_id": candidate_run_id,
            "tag_filter": tag_filter,
            "date_from": date_from.isoformat().replace("+00:00", "Z") if date_from else "",
            "date_to": date_to.isoformat().replace("+00:00", "Z") if date_to else "",
        },
    }


def _get_compare_snapshot(runs_root: Path, snapshot_id: str) -> tuple[dict[str, Any], HTTPStatus]:
    normalized_id = snapshot_id.strip()
    if not normalized_id or not SAFE_COMPARE_SNAPSHOT_ID_RE.fullmatch(normalized_id):
        return {
            "error": {
                "code": "invalid_compare_snapshot_id",
                "message": "snapshot id is invalid",
            }
        }, HTTPStatus.BAD_REQUEST

    path = _compare_snapshots_root(runs_root) / f"{normalized_id}.json"
    if not path.exists() or not path.is_file():
        return {
            "error": {
                "code": "compare_snapshot_not_found",
                "message": "compare snapshot not found",
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.NOT_FOUND

    try:
        record = _load_compare_snapshot_record(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "error": {
                "code": "compare_snapshot_unreadable",
                "message": str(exc),
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.UNPROCESSABLE_ENTITY

    integrity = _compare_snapshot_integrity_status(record)
    return {
        "snapshot": _compare_snapshot_metadata(record, path, integrity=integrity),
        "compare_snapshot": record.get("snapshot") if isinstance(record.get("snapshot"), dict) else {},
        "compare_payload": record.get("compare_payload") if isinstance(record.get("compare_payload"), dict) else {},
        "markdown": str(record.get("markdown") or ""),
        "integrity": integrity,
    }, HTTPStatus.OK


def _rename_compare_snapshot(runs_root: Path, snapshot_id: str, display_name: str) -> tuple[dict[str, Any], HTTPStatus]:
    normalized_id = snapshot_id.strip()
    if not normalized_id or not SAFE_COMPARE_SNAPSHOT_ID_RE.fullmatch(normalized_id):
        return {
            "error": {
                "code": "invalid_compare_snapshot_id",
                "message": "snapshot id is invalid",
            }
        }, HTTPStatus.BAD_REQUEST

    normalized_name = str(display_name or "").strip()
    if not normalized_name:
        return {
            "error": {
                "code": "invalid_compare_snapshot_name",
                "message": "display_name is required",
            }
        }, HTTPStatus.BAD_REQUEST

    path = _compare_snapshots_root(runs_root) / f"{normalized_id}.json"
    if not path.exists() or not path.is_file():
        return {
            "error": {
                "code": "compare_snapshot_not_found",
                "message": "compare snapshot not found",
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.NOT_FOUND

    try:
        record = _load_compare_snapshot_record(path)
        record["display_name"] = normalized_name
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "error": {
                "code": "compare_snapshot_update_failed",
                "message": str(exc),
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.UNPROCESSABLE_ENTITY

    return {"snapshot": _compare_snapshot_metadata(record, path)}, HTTPStatus.OK


def _update_compare_snapshot_metadata(
    runs_root: Path,
    snapshot_id: str,
    *,
    display_name: Any = None,
    pinned: Any = None,
    archived: Any = None,
    tags: Any = None,
) -> tuple[dict[str, Any], HTTPStatus]:
    normalized_id = snapshot_id.strip()
    if not normalized_id or not SAFE_COMPARE_SNAPSHOT_ID_RE.fullmatch(normalized_id):
        return {
            "error": {
                "code": "invalid_compare_snapshot_id",
                "message": "snapshot id is invalid",
            }
        }, HTTPStatus.BAD_REQUEST

    path = _compare_snapshots_root(runs_root) / f"{normalized_id}.json"
    if not path.exists() or not path.is_file():
        return {
            "error": {
                "code": "compare_snapshot_not_found",
                "message": "compare snapshot not found",
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.NOT_FOUND

    try:
        record = _load_compare_snapshot_record(path)
        if display_name is not None:
            normalized_name = str(display_name or "").strip()
            if not normalized_name:
                return {
                    "error": {
                        "code": "invalid_compare_snapshot_name",
                        "message": "display_name is required",
                    }
                }, HTTPStatus.BAD_REQUEST
            record["display_name"] = normalized_name
        if pinned is not None:
            record["pinned"] = bool(pinned)
        if archived is not None:
            is_archived = bool(archived)
            record["archived"] = is_archived
            record["archived_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                if is_archived
                else ""
            )
        if tags is not None:
            record["tags"] = _normalize_compare_snapshot_tags(tags)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "error": {
                "code": "compare_snapshot_update_failed",
                "message": str(exc),
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.UNPROCESSABLE_ENTITY

    return {"snapshot": _compare_snapshot_metadata(record, path)}, HTTPStatus.OK


def _bulk_update_compare_snapshots(
    runs_root: Path,
    *,
    snapshot_ids: list[str],
    action: str,
    display_name: Any = None,
    pinned: Any = None,
    archived: Any = None,
    tags: Any = None,
) -> tuple[dict[str, Any], HTTPStatus]:
    if action not in {"metadata", "delete"}:
        return {
            "error": {
                "code": "invalid_compare_snapshot_bulk_action",
                "message": "bulk action must be 'metadata' or 'delete'",
            }
        }, HTTPStatus.BAD_REQUEST

    normalized_ids: list[str] = []
    for raw_id in snapshot_ids:
        snapshot_id = str(raw_id or "").strip()
        if not snapshot_id or not SAFE_COMPARE_SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            return {
                "error": {
                    "code": "invalid_compare_snapshot_id",
                    "message": f"snapshot id is invalid: {snapshot_id or '<empty>'}",
                }
            }, HTTPStatus.BAD_REQUEST
        normalized_ids.append(snapshot_id)

    if not normalized_ids:
        return {
            "error": {
                "code": "missing_compare_snapshot_ids",
                "message": "at least one snapshot id is required",
            }
        }, HTTPStatus.BAD_REQUEST

    updated: list[dict[str, Any]] = []
    deleted: list[str] = []
    errors: list[dict[str, Any]] = []
    for snapshot_id in normalized_ids:
        if action == "delete":
            body, status = _delete_compare_snapshot(runs_root, snapshot_id)
            if status == HTTPStatus.OK:
                deleted.append(snapshot_id)
            else:
                errors.append({"snapshot_id": snapshot_id, "error": body.get("error", body)})
            continue

        body, status = _update_compare_snapshot_metadata(
            runs_root,
            snapshot_id,
            display_name=display_name,
            pinned=pinned,
            archived=archived,
            tags=tags,
        )
        if status == HTTPStatus.OK:
            updated.append(body.get("snapshot") if isinstance(body.get("snapshot"), dict) else {"snapshot_id": snapshot_id})
        else:
            errors.append({"snapshot_id": snapshot_id, "error": body.get("error", body)})

    if errors and not updated and not deleted:
        return {
            "error": {
                "code": "compare_snapshot_bulk_failed",
                "message": "bulk snapshot action failed",
                "errors": errors,
            }
        }, HTTPStatus.UNPROCESSABLE_ENTITY

    return {
        "action": action,
        "updated": updated,
        "deleted_snapshot_ids": deleted,
        "errors": errors,
        "summary": {
            "requested": len(normalized_ids),
            "updated": len(updated),
            "deleted": len(deleted),
            "failed": len(errors),
        },
    }, HTTPStatus.OK


def _delete_compare_snapshot(runs_root: Path, snapshot_id: str) -> tuple[dict[str, Any], HTTPStatus]:
    normalized_id = snapshot_id.strip()
    if not normalized_id or not SAFE_COMPARE_SNAPSHOT_ID_RE.fullmatch(normalized_id):
        return {
            "error": {
                "code": "invalid_compare_snapshot_id",
                "message": "snapshot id is invalid",
            }
        }, HTTPStatus.BAD_REQUEST

    path = _compare_snapshots_root(runs_root) / f"{normalized_id}.json"
    md_path = path.with_suffix(".md")
    if not path.exists() or not path.is_file():
        return {
            "error": {
                "code": "compare_snapshot_not_found",
                "message": "compare snapshot not found",
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.NOT_FOUND

    try:
        path.unlink()
        if md_path.exists():
            md_path.unlink()
    except OSError as exc:
        return {
            "error": {
                "code": "compare_snapshot_delete_failed",
                "message": str(exc),
                "snapshot_id": normalized_id,
            }
        }, HTTPStatus.UNPROCESSABLE_ENTITY

    return {"deleted": True, "snapshot_id": normalized_id}, HTTPStatus.OK


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
                        "scorecard": True,
                        "autonomous_quality_gate_snapshot": True,
                        "experiment_log": True,
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

        if path == "/api/scorecard/latest":
            self._json_response(_latest_scorecard_summary(self.config.runs_root))
            return

        if path == "/api/autonomous/quality-gate/latest":
            self._json_response(_latest_quality_gate_snapshot(self.config.runs_root))
            return

        if path == "/api/autonomous/trust/latest":
            self._json_response(_latest_trust_snapshot(self.config.runs_root))
            return

        if path == "/api/autonomous/trust/trends":
            query = parse_qs(parsed.query)
            window = _parse_trend_window(str((query.get("window") or [""])[0]))
            self._json_response(_trust_trends(self.config.runs_root, window))
            return

        if path == "/api/runs":
            self._json_response({"runs": _list_runs(self.config.runs_root)})
            return

        if path == "/api/runs/compare/snapshots":
            query = parse_qs(parsed.query)
            payload = _list_compare_snapshots(
                self.config.runs_root,
                query=str((query.get("query") or [""])[0]).strip(),
                sort=_normalize_compare_snapshot_sort(str((query.get("sort") or ["newest"])[0])),
                archive_filter=_normalize_compare_snapshot_archive_filter(str((query.get("archived") or ["active"])[0])),
                pinned_filter=_normalize_compare_snapshot_pinned_filter(str((query.get("pinned") or ["all"])[0])),
                baseline_run_id=str((query.get("baseline_run_id") or [""])[0]).strip(),
                candidate_run_id=str((query.get("candidate_run_id") or [""])[0]).strip(),
                tag_filter=str((query.get("tag") or [""])[0]).strip(),
                date_from=_parse_compare_snapshot_filter_datetime(str((query.get("date_from") or [""])[0])),
                date_to=_parse_compare_snapshot_filter_datetime(str((query.get("date_to") or [""])[0])),
                page=_parse_compare_snapshot_page(str((query.get("page") or ["1"])[0])),
                page_size=_parse_compare_snapshot_page_size(str((query.get("page_size") or [str(DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE)])[0])),
            )
            self._audit_then_respond(
                body=payload,
                status=HTTPStatus.OK,
                audit_event=self._build_compare_snapshot_audit_event(
                    action="compare_snapshot_list",
                    payload={
                        "query": str((query.get("query") or [""])[0]).strip(),
                        "sort": str((query.get("sort") or ["newest"])[0]).strip(),
                        "archived": str((query.get("archived") or ["active"])[0]).strip(),
                        "pinned": str((query.get("pinned") or ["all"])[0]).strip(),
                        "page": str((query.get("page") or ["1"])[0]).strip(),
                        "page_size": str((query.get("page_size") or [str(DEFAULT_COMPARE_SNAPSHOT_PAGE_SIZE)])[0]).strip(),
                    },
                    result_status="listed",
                ),
            )
            return

        if path.startswith("/api/runs/compare/snapshots/"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/")).strip("/")
            payload, status = _get_compare_snapshot(self.config.runs_root, snapshot_id)
            self._audit_then_respond(
                body=payload,
                status=status,
                audit_event=self._build_compare_snapshot_audit_event(
                    action="compare_snapshot_open",
                    payload={"snapshot_id": snapshot_id},
                    result_status="opened" if status == HTTPStatus.OK else "failed",
                ),
            )
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

        if path == "/api/experiment-log":
            query = parse_qs(parsed.query)
            task_id = str((query.get("task_id") or [""])[0]).strip() or None
            run_id_filter = str((query.get("run_id") or [""])[0]).strip() or None
            payload, status = _experiment_log_for_latest_or_run(self.config.runs_root, run_id=run_id_filter, task_id=task_id)
            self._json_response(payload, status=status)
            return

        if path.startswith("/api/runs/") and path.endswith("/experiment-log"):
            run_id = unquote(path.removeprefix("/api/runs/").removesuffix("/experiment-log")).strip("/")
            if not run_id:
                self._json_response({"error": "run id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            query = parse_qs(parsed.query)
            task_id = str((query.get("task_id") or [""])[0]).strip() or None
            payload, status = _experiment_log_for_latest_or_run(self.config.runs_root, run_id=run_id, task_id=task_id)
            self._json_response(payload, status=status)
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
        if path == "/api/runs/compare/snapshots":
            self._handle_compare_snapshot_save()
            return
        if path == "/api/runs/compare/snapshots/bulk":
            self._handle_compare_snapshot_bulk()
            return
        if path.startswith("/api/runs/compare/snapshots/") and path.endswith("/metadata"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/").removesuffix("/metadata")).strip("/")
            self._handle_compare_snapshot_metadata(snapshot_id)
            return
        if path.startswith("/api/runs/compare/snapshots/") and path.endswith("/rename"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/").removesuffix("/rename")).strip("/")
            self._handle_compare_snapshot_rename(snapshot_id)
            return
        if path.startswith("/api/runs/compare/snapshots/") and path.endswith("/delete"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/").removesuffix("/delete")).strip("/")
            self._handle_compare_snapshot_delete(snapshot_id)
            return
        self._json_response({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/runs/compare/snapshots/"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/")).strip("/")
            self._handle_compare_snapshot_metadata(snapshot_id)
            return
        self._json_response({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/runs/compare/snapshots/"):
            snapshot_id = unquote(path.removeprefix("/api/runs/compare/snapshots/")).strip("/")
            self._handle_compare_snapshot_delete(snapshot_id)
            return
        self._json_response({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _build_compare_snapshot_audit_event(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        result_status: str,
    ) -> dict[str, Any]:
        auth = _resolve_request_auth(headers=self.headers, payload=payload, action=action)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "action": action,
            "result_status": result_status,
            "role": auth.role,
            "auth": {
                "source": auth.source,
                "subject": auth.subject,
                "scope": auth.scope,
                "policy_name": auth.policy_name,
                "policy_allowed_roles": auth.policy_allowed_roles,
            },
            "payload": payload,
        }

    def _handle_run_control(self, *, action: str) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return

        auth = _resolve_request_auth(headers=self.headers, payload=payload, action=action)
        role = auth.role
        execute = bool(payload.get("execute", False))
        correlation_id = _coerce_correlation_id_for_audit(payload.get("correlation_id"))
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
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

    def _handle_compare_snapshot_save(self) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return
        try:
            snapshot = _persist_compare_snapshot(self.config.runs_root, payload)
        except GuiApiError as exc:
            self._json_response(
                {"error": _error_payload("invalid_compare_snapshot", str(exc))},
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
            return
        except OSError as exc:
            self._json_response(
                {"error": _error_payload("compare_snapshot_persist_failed", str(exc))},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._audit_then_respond(
            body={"snapshot": snapshot},
            status=HTTPStatus.OK,
            audit_event=self._build_compare_snapshot_audit_event(
                action="compare_snapshot_save",
                payload={
                    "display_name": payload.get("display_name"),
                    "pinned": payload.get("pinned"),
                    "archived": payload.get("archived"),
                    "tags": _normalize_compare_snapshot_tags(payload.get("tags")),
                    "snapshot_id": snapshot.get("snapshot_id"),
                },
                result_status="saved",
            ),
        )

    def _handle_compare_snapshot_rename(self, snapshot_id: str) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return
        display_name = str(payload.get("display_name") or "")
        body, status = _rename_compare_snapshot(self.config.runs_root, snapshot_id, display_name)
        self._audit_then_respond(
            body=body,
            status=status,
            audit_event=self._build_compare_snapshot_audit_event(
                action="compare_snapshot_rename",
                payload={"snapshot_id": snapshot_id, "display_name": display_name},
                result_status="updated" if status == HTTPStatus.OK else "failed",
            ),
        )

    def _handle_compare_snapshot_metadata(self, snapshot_id: str) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return
        body, status = _update_compare_snapshot_metadata(
            self.config.runs_root,
            snapshot_id,
            display_name=payload.get("display_name"),
            pinned=payload.get("pinned"),
            archived=payload.get("archived"),
            tags=payload.get("tags"),
        )
        self._audit_then_respond(
            body=body,
            status=status,
            audit_event=self._build_compare_snapshot_audit_event(
                action="compare_snapshot_metadata",
                payload={
                    "snapshot_id": snapshot_id,
                    "display_name": payload.get("display_name"),
                    "pinned": payload.get("pinned"),
                    "archived": payload.get("archived"),
                    "tags": _normalize_compare_snapshot_tags(payload.get("tags")),
                },
                result_status="updated" if status == HTTPStatus.OK else "failed",
            ),
        )

    def _handle_compare_snapshot_delete(self, snapshot_id: str) -> None:
        body, status = _delete_compare_snapshot(self.config.runs_root, snapshot_id)
        self._audit_then_respond(
            body=body,
            status=status,
            audit_event=self._build_compare_snapshot_audit_event(
                action="compare_snapshot_delete",
                payload={"snapshot_id": snapshot_id},
                result_status="deleted" if status == HTTPStatus.OK else "failed",
            ),
        )

    def _handle_compare_snapshot_bulk(self) -> None:
        payload = self._read_json_payload()
        if payload is None:
            return
        snapshot_ids = payload.get("snapshot_ids") if isinstance(payload.get("snapshot_ids"), list) else []
        action = str(payload.get("action") or "metadata").strip().lower()
        body, status = _bulk_update_compare_snapshots(
            self.config.runs_root,
            snapshot_ids=snapshot_ids,
            action=action,
            display_name=payload.get("display_name"),
            pinned=payload.get("pinned"),
            archived=payload.get("archived"),
            tags=payload.get("tags"),
        )
        self._audit_then_respond(
            body=body,
            status=status,
            audit_event=self._build_compare_snapshot_audit_event(
                action="compare_snapshot_bulk",
                payload={
                    "snapshot_ids": [str(item or "").strip() for item in snapshot_ids],
                    "action": action,
                    "display_name": payload.get("display_name"),
                    "pinned": payload.get("pinned"),
                    "archived": payload.get("archived"),
                    "tags": _normalize_compare_snapshot_tags(payload.get("tags")),
                },
                result_status="updated" if status == HTTPStatus.OK else "failed",
            ),
        )

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
