from __future__ import annotations

from collections import Counter
from typing import Any


def normalize_run_trace(run_trace: dict[str, Any] | None) -> dict[str, Any]:
    trace = run_trace if isinstance(run_trace, dict) else {}

    timeline = _normalize_phase_timeline(trace)

    started_at = ""
    completed_at = ""
    for ev in trace.get("events", []) if isinstance(trace.get("events"), list) else []:
        if not isinstance(ev, dict):
            continue
        event_type = str(ev.get("event_type") or ev.get("event") or "")
        ts = str(ev.get("timestamp") or "")
        if event_type == "run.start" and ts and not started_at:
            started_at = ts
        elif event_type == "run.completed" and ts and not completed_at:
            completed_at = ts

    return {
        "model": _coerce_non_empty(
            trace.get("model"),
            trace.get("llm_model"),
            _dig_str(trace, "llm", "model"),
            _dig_str(trace, "config", "llm", "model"),
        ),
        "profile": _coerce_non_empty(trace.get("profile"), _dig_str(trace, "run", "profile")),
        "run_id": _coerce_non_empty(trace.get("run_id")),
        "request_id": _coerce_non_empty(trace.get("request_id")),
        "total_elapsed_ms": _to_int(trace.get("total_elapsed_ms"), default=0),
        "event_count": _to_int(trace.get("event_count"), default=len(trace.get("events", [])) if isinstance(trace.get("events"), list) else 0),
        "started_at": started_at,
        "completed_at": completed_at,
        "phase_timeline": timeline,
    }


def normalize_tasks(quality_index: dict[str, Any] | None) -> list[dict[str, Any]]:
    quality = quality_index if isinstance(quality_index, dict) else {}
    raw_tasks = quality.get("tasks")
    if not isinstance(raw_tasks, list):
        return []

    by_task: dict[str, dict[str, Any]] = {}
    for row in raw_tasks:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id") or row.get("id") or "").strip()
        if not task_id:
            continue

        bucket = by_task.setdefault(
            task_id,
            {
                "task_id": task_id,
                "status": "unknown",
                "attempts": 0,
                "hard_failures": 0,
                "soft_failures": 0,
                "duration_ms": 0,
                "last_attempt": 0,
            },
        )

        attempt = _to_int(row.get("attempt"), default=0)
        status = str(row.get("status") or "unknown")
        bucket["attempts"] = max(bucket["attempts"], attempt, bucket["attempts"] + (1 if attempt == 0 else 0))
        bucket["hard_failures"] += _to_int(row.get("hard_failures"), default=0)
        bucket["soft_failures"] += _to_int(row.get("soft_failures"), default=0)
        bucket["duration_ms"] += _to_int(row.get("duration_ms"), default=0)

        if attempt >= bucket["last_attempt"]:
            bucket["last_attempt"] = attempt
            bucket["status"] = status

    out = list(by_task.values())
    out.sort(key=lambda r: r["task_id"])
    return out


def normalize_validation(
    final_validation: dict[str, Any] | None,
    quality_index: dict[str, Any] | None,
) -> dict[str, Any]:
    final_dict = final_validation if isinstance(final_validation, dict) else {}
    quality = quality_index if isinstance(quality_index, dict) else {}

    rows = _extract_validation_rows(final_dict, quality)

    by_status = Counter(row["status"] for row in rows)
    by_validator = Counter(row["name"] for row in rows)

    return {
        "summary": {
            "total": len(rows),
            "passed": by_status.get("passed", 0),
            "failed": by_status.get("failed", 0),
            "soft_fail": by_status.get("soft_fail", 0),
            "skipped": by_status.get("skipped_dependency", 0),
            "blocking_failed": sum(1 for row in rows if (not row["ok"]) and row["status"] not in {"soft_fail", "skipped_dependency"}),
            "by_status": dict(by_status),
            "by_validator": dict(by_validator),
        },
        "validator_cards": rows,
    }


def _extract_validation_rows(final_dict: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    # final validation artifact variants (preferred source)
    for key in ("validation", "validations", "results", "rows"):
        value = final_dict.get(key)
        if isinstance(value, list):
            candidates.extend([row for row in value if isinstance(row, dict)])

    # legacy nested shape: {"final": {"validations": [...]}}
    final_obj = final_dict.get("final")
    if isinstance(final_obj, dict):
        vals = final_obj.get("validations")
        if isinstance(vals, list):
            candidates.extend([row for row in vals if isinstance(row, dict)])

    # fallback to quality_index.final.validations only when final artifact missing
    if not candidates:
        quality_final = quality.get("final")
        if isinstance(quality_final, dict):
            vals = quality_final.get("validations")
            if isinstance(vals, list):
                candidates.extend([row for row in vals if isinstance(row, dict)])

    normalized: list[dict[str, Any]] = []
    for row in candidates:
        name = str(row.get("name") or row.get("validator") or "").strip()
        if not name:
            continue
        status = str(row.get("status") or ("passed" if row.get("ok") else "failed")).strip().lower() or "failed"
        ok = bool(row.get("ok", status in {"passed", "soft_pass"}))

        normalized.append(
            {
                "name": name,
                "status": status,
                "ok": ok,
                "returncode": _to_int(row.get("returncode"), default=0 if ok else 1),
                "duration_ms": _to_int(row.get("duration_ms"), default=0),
                "error_classification": str(row.get("error_classification") or ""),
                "note": str(row.get("note") or ""),
                "phase": str(row.get("phase") or "final"),
                "stdout": str(row.get("stdout") or ""),
                "stderr": str(row.get("stderr") or ""),
            }
        )

    return normalized


def _normalize_phase_timeline(trace: dict[str, Any]) -> list[dict[str, Any]]:
    phases = trace.get("phases")
    if isinstance(phases, list) and phases:
        out: list[dict[str, Any]] = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            phase_name = str(phase.get("phase") or phase.get("name") or "").strip()
            if not phase_name:
                continue
            duration_ms = _to_int(phase.get("duration_ms"), default=0)
            if duration_ms <= 0:
                start_ms = _to_int(phase.get("start_ms"), default=0)
                end_ms = _to_int(phase.get("end_ms"), default=0)
                if end_ms >= start_ms:
                    duration_ms = end_ms - start_ms
            out.append(
                {
                    "phase": phase_name,
                    "duration_ms": duration_ms,
                    "status": str(phase.get("status") or ""),
                }
            )
        if out:
            return out

    timeline = trace.get("phase_timeline")
    if isinstance(timeline, list):
        out = []
        for phase in timeline:
            if not isinstance(phase, dict):
                continue
            phase_name = str(phase.get("phase") or phase.get("name") or "").strip()
            if not phase_name:
                continue
            out.append(
                {
                    "phase": phase_name,
                    "duration_ms": _to_int(phase.get("duration_ms"), default=0),
                    "status": str(phase.get("status") or ""),
                }
            )
        if out:
            return out

    # fallback: reconstruct from event stream
    events = trace.get("events")
    if isinstance(events, list):
        starts: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            event_type = str(ev.get("event_type") or ev.get("event") or "")
            phase_name = str(ev.get("phase") or "").strip()
            elapsed_ms = _to_int(ev.get("elapsed_ms"), default=0)
            if not phase_name:
                continue
            if event_type == "phase.start":
                starts[phase_name] = elapsed_ms
            elif event_type == "phase.end":
                started = starts.get(phase_name, elapsed_ms)
                out.append(
                    {
                        "phase": phase_name,
                        "duration_ms": max(0, elapsed_ms - started),
                        "status": str(ev.get("status") or ""),
                    }
                )
        if out:
            return out

    return []


def _dig_str(data: dict[str, Any], *path: str) -> str:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return str(cur).strip() if cur is not None else ""


def _to_int(value: Any, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
