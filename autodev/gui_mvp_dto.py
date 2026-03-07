from __future__ import annotations

from collections import Counter
from typing import Any

from .run_status import normalize_run_status


def normalize_run_trace(run_trace: dict[str, Any] | None) -> dict[str, Any]:
    trace = run_trace if isinstance(run_trace, dict) else {}

    timeline = _normalize_phase_timeline(trace)
    raw_events = _extract_raw_events(trace)
    timeline_events = _normalize_timeline_events(raw_events, timeline)
    started_at, completed_at = _extract_run_bounds(timeline_events)

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
        "event_count": _to_int(trace.get("event_count"), default=len(raw_events)),
        "started_at": started_at,
        "completed_at": completed_at,
        "phase_timeline": timeline,
        "timeline_events": timeline_events,
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


def normalize_run_comparison(left_detail: dict[str, Any] | None, right_detail: dict[str, Any] | None) -> dict[str, Any]:
    left = _normalize_comparison_side(left_detail)
    right = _normalize_comparison_side(right_detail)

    left_blockers = set(left["blockers"])
    right_blockers = set(right["blockers"])

    left_validators = left["validator_outcomes"]
    right_validators = right["validator_outcomes"]

    shared_validator_names = sorted(set(left_validators.keys()) & set(right_validators.keys()))
    changed = [
        {
            "name": name,
            "left": left_validators[name],
            "right": right_validators[name],
        }
        for name in shared_validator_names
        if left_validators[name] != right_validators[name]
    ]

    only_left = [{"name": name, "status": left_validators[name]} for name in sorted(set(left_validators.keys()) - set(right_validators.keys()))]
    only_right = [{"name": name, "status": right_validators[name]} for name in sorted(set(right_validators.keys()) - set(left_validators.keys()))]

    diff_fields = [
        field
        for field in ("total_task_attempts", "hard_failures", "soft_failures")
        if left["totals"].get(field) != right["totals"].get(field)
    ]

    return {
        "schema_version": "shw-012-v1",
        "left": left,
        "right": right,
        "diff": {
            "status_changed": left["status"] != right["status"],
            "totals_changed_fields": diff_fields,
            "blockers": {
                "changed": left_blockers != right_blockers,
                "only_left": sorted(left_blockers - right_blockers),
                "only_right": sorted(right_blockers - left_blockers),
            },
            "validation": {
                "changed": changed,
                "only_left": only_left,
                "only_right": only_right,
                "counts_delta": {
                    "total": right["validation"]["total"] - left["validation"]["total"],
                    "passed": right["validation"]["passed"] - left["validation"]["passed"],
                    "failed": right["validation"]["failed"] - left["validation"]["failed"],
                    "soft_fail": right["validation"]["soft_fail"] - left["validation"]["soft_fail"],
                    "skipped": right["validation"]["skipped"] - left["validation"]["skipped"],
                },
            },
        },
    }


def normalize_run_comparison_summary(run_detail: dict[str, Any] | None) -> dict[str, Any]:
    detail = run_detail if isinstance(run_detail, dict) else {}

    quality_index = detail.get("quality_index") if isinstance(detail.get("quality_index"), dict) else {}
    metadata = detail.get("metadata") if isinstance(detail.get("metadata"), dict) else {}
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    project = summary.get("project") if isinstance(summary.get("project"), dict) else {}
    blockers = detail.get("blockers") if isinstance(detail.get("blockers"), list) else []
    phase_timeline = detail.get("phase_timeline") if isinstance(detail.get("phase_timeline"), list) else []

    normalized_status = normalize_run_status(quality_index=quality_index, default=str(detail.get("status") or "unknown"))

    validation_summary = _extract_validation_summary(detail)

    timeline_duration = 0
    phase_count = 0
    for row in phase_timeline:
        if not isinstance(row, dict):
            continue
        phase_count += 1
        timeline_duration += _to_int(row.get("duration_ms"), default=0)

    return {
        "run_id": str(detail.get("run_id") or ""),
        "status": normalized_status,
        "project_type": _coerce_non_empty(project.get("type"), detail.get("project_type")),
        "profile": _coerce_non_empty(metadata.get("profile"), _dig_str(summary, "profile", "name")),
        "model": _coerce_non_empty(metadata.get("model"), detail.get("model")),
        "started_at": _coerce_non_empty(metadata.get("started_at"), detail.get("started_at")),
        "completed_at": _coerce_non_empty(metadata.get("completed_at"), detail.get("ended_at")),
        "updated_at": _coerce_non_empty(detail.get("updated_at")),
        "totals": {
            "total_task_attempts": _pick_int(totals, "total_task_attempts", "total_attempts", "attempts"),
            "hard_failures": _pick_int(totals, "hard_failures", "hard_failure_count", "hard"),
            "soft_failures": _pick_int(totals, "soft_failures", "soft_failure_count", "soft"),
            "task_count": len(detail.get("tasks")) if isinstance(detail.get("tasks"), list) else 0,
            "blocker_count": len(blockers),
        },
        "validation": validation_summary,
        "timeline": {
            "phase_count": phase_count,
            "total_duration_ms": timeline_duration,
        },
        "blockers": [str(b) for b in blockers],
    }


def _normalize_comparison_side(detail: dict[str, Any] | None) -> dict[str, Any]:
    data = detail if isinstance(detail, dict) else {}

    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    project = summary.get("project") if isinstance(summary.get("project"), dict) else {}
    profile = summary.get("profile") if isinstance(summary.get("profile"), dict) else {}

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    validation_normalized = (
        data.get("validation_normalized")
        if isinstance(data.get("validation_normalized"), dict)
        else {}
    )
    validation_summary = (
        validation_normalized.get("summary")
        if isinstance(validation_normalized.get("summary"), dict)
        else {}
    )

    blockers = [str(item) for item in (data.get("blockers") if isinstance(data.get("blockers"), list) else [])]

    outcomes: dict[str, str] = {}
    validator_cards = validation_normalized.get("validator_cards")
    if isinstance(validator_cards, list):
        for row in validator_cards:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("validator") or "").strip()
            if not name:
                continue
            outcomes[name] = _normalize_validator_status(row.get("status"), row.get("ok"))

    return {
        "run_id": _coerce_non_empty(data.get("run_id"), metadata.get("run_id")),
        "status": _coerce_non_empty(data.get("status"), "unknown").lower(),
        "project_type": _coerce_non_empty(project.get("type")),
        "profile": _coerce_non_empty(profile.get("name"), metadata.get("profile")),
        "model": _coerce_non_empty(data.get("model"), metadata.get("model")),
        "totals": {
            "total_task_attempts": _to_int(totals.get("total_task_attempts"), default=0),
            "hard_failures": _to_int(totals.get("hard_failures"), default=0),
            "soft_failures": _to_int(totals.get("soft_failures"), default=0),
        },
        "blockers": blockers,
        "validation": {
            "total": _to_int(validation_summary.get("total"), default=0),
            "passed": _to_int(validation_summary.get("passed"), default=0),
            "failed": _to_int(validation_summary.get("failed"), default=0),
            "soft_fail": _to_int(validation_summary.get("soft_fail"), default=0),
            "skipped": _to_int(validation_summary.get("skipped"), default=0),
        },
        "validator_outcomes": outcomes,
    }


def _normalize_validator_status(status: Any, ok: Any) -> str:
    text = str(status or "").strip().lower()
    if text in {"ok", "success", "pass"}:
        return "passed"
    if text:
        return text
    if ok is True:
        return "passed"
    if ok is False:
        return "failed"
    return "unknown"


def _extract_validation_rows(final_dict: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    final_rows: list[dict[str, Any]] = []

    # final validation artifact variants (preferred source)
    for key in ("validation", "validations", "results", "rows"):
        value = final_dict.get(key)
        if isinstance(value, list):
            for row in value:
                normalized = _normalize_validation_row(
                    row,
                    phase_default="final",
                    scope="final",
                    task_id="",
                    artifact_path=".autodev/task_final_last_validation.json",
                )
                if normalized:
                    final_rows.append(normalized)

    # legacy nested shape: {"final": {"validations": [...]}}
    final_obj = final_dict.get("final")
    if isinstance(final_obj, dict):
        vals = final_obj.get("validations")
        if isinstance(vals, list):
            for row in vals:
                normalized = _normalize_validation_row(
                    row,
                    phase_default="final",
                    scope="final",
                    task_id="",
                    artifact_path=".autodev/task_final_last_validation.json",
                )
                if normalized:
                    final_rows.append(normalized)

    # fallback to quality_index.final.validations only when final artifact missing
    if not final_rows:
        quality_final = quality.get("final")
        if isinstance(quality_final, dict):
            vals = quality_final.get("validations")
            if isinstance(vals, list):
                for row in vals:
                    normalized = _normalize_validation_row(
                        row,
                        phase_default="final",
                        scope="final",
                        task_id="",
                        artifact_path=".autodev/task_final_last_validation.json",
                    )
                    if normalized:
                        final_rows.append(normalized)

    per_task_rows: list[dict[str, Any]] = []
    tasks = quality.get("tasks")
    if isinstance(tasks, list):
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or task.get("id") or "").strip()
            if not task_id:
                continue
            last_validation = task.get("last_validation")
            if not isinstance(last_validation, list):
                continue
            for row in last_validation:
                normalized = _normalize_validation_row(
                    row,
                    phase_default="implementation",
                    scope="task",
                    task_id=task_id,
                    artifact_path=f".autodev/task_{task_id}_last_validation.json",
                )
                if normalized:
                    per_task_rows.append(normalized)

    # Keep final rows first (authoritative verdict), append per-task rows for triage deep-link context.
    return final_rows + per_task_rows


def _normalize_validation_row(
    row: Any,
    *,
    phase_default: str,
    scope: str,
    task_id: str,
    artifact_path: str,
) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None

    name = str(row.get("name") or row.get("validator") or "").strip()
    if not name:
        return None

    status = _normalize_validation_status(row.get("status"), row.get("ok"))
    ok = bool(row.get("ok", status in {"passed", "soft_pass"}))

    return {
        "name": name,
        "status": status,
        "ok": ok,
        "returncode": _to_int(row.get("returncode"), default=0 if ok else 1),
        "duration_ms": _to_int(row.get("duration_ms"), default=0),
        "error_classification": str(row.get("error_classification") or ""),
        "note": str(row.get("note") or row.get("message") or row.get("error") or ""),
        "phase": str(row.get("phase") or phase_default),
        "stdout": str(row.get("stdout") or ""),
        "stderr": str(row.get("stderr") or ""),
        "scope": scope,
        "task_id": task_id,
        "artifact_path": artifact_path,
    }


def _extract_validation_summary(detail: dict[str, Any]) -> dict[str, int]:
    normalized = detail.get("validation_normalized")
    if isinstance(normalized, dict):
        summary = normalized.get("summary")
        if isinstance(summary, dict):
            return {
                "total": _to_int(summary.get("total"), default=0),
                "passed": _to_int(summary.get("passed"), default=0),
                "failed": _to_int(summary.get("failed"), default=0),
                "soft_fail": _to_int(summary.get("soft_fail"), default=0),
                "skipped": _to_int(summary.get("skipped"), default=0),
                "blocking_failed": _to_int(summary.get("blocking_failed"), default=0),
            }

    source = detail.get("validation")
    rows: list[dict[str, Any]] = []
    if isinstance(source, dict):
        for key in ("validation", "validations", "results", "rows"):
            val = source.get(key)
            if isinstance(val, list):
                rows = [row for row in val if isinstance(row, dict)]
                if rows:
                    break

    counts = Counter()
    blocking_failed = 0
    for row in rows:
        status = _normalize_validation_status(row.get("status"), row.get("ok"))
        counts[status] += 1
        if status not in {"passed", "soft_fail", "skipped_dependency"}:
            blocking_failed += 1

    return {
        "total": len(rows),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "soft_fail": counts.get("soft_fail", 0),
        "skipped": counts.get("skipped_dependency", 0),
        "blocking_failed": blocking_failed,
    }


def _normalize_validation_status(status: Any, ok: Any) -> str:
    text = str(status or "").strip().lower()
    if text in {"pass", "passed", "ok", "success", "succeeded", "soft_pass"}:
        return "passed"
    if text in {"fail", "failed", "error", "errored"}:
        return "failed"
    if text in {"soft_fail", "soft-fail", "softfail", "warn", "warning"}:
        return "soft_fail"
    if text in {"skipped", "skip", "skipped_dependency"}:
        return "skipped_dependency"
    if ok is True:
        return "passed"
    if ok is False:
        return "failed"
    return "unknown"


def _extract_raw_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("events", "timeline_events", "trace_events"):
        rows = trace.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _normalize_timeline_events(raw_events: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for ev in raw_events:
        normalized_type = _normalize_event_type(ev.get("event_type") or ev.get("event") or ev.get("type") or ev.get("name"))
        if not normalized_type:
            continue

        out.append(
            {
                "event_type": normalized_type,
                "event_category": _event_category(normalized_type),
                "phase": _coerce_non_empty(ev.get("phase")),
                "status": _coerce_non_empty(ev.get("status"), ev.get("result"), ev.get("outcome")),
                "timestamp": _coerce_non_empty(ev.get("timestamp"), ev.get("ts"), ev.get("time")),
                "elapsed_ms": _to_int(ev.get("elapsed_ms"), default=0),
                "source": "event_stream",
            }
        )

    # Modern artifacts often provide only phase summary rows; synthesize stable phase.end events.
    existing_phase_end = {row.get("phase") for row in out if row.get("event_type") == "phase.end" and row.get("phase")}
    for phase in timeline:
        phase_name = str(phase.get("phase") or "").strip()
        if not phase_name or phase_name in existing_phase_end:
            continue
        out.append(
            {
                "event_type": "phase.end",
                "event_category": "phase",
                "phase": phase_name,
                "status": str(phase.get("status") or ""),
                "timestamp": "",
                "elapsed_ms": _to_int(phase.get("duration_ms"), default=0),
                "source": "phase_timeline",
            }
        )

    return out


def _extract_run_bounds(events: list[dict[str, Any]]) -> tuple[str, str]:
    started_at = ""
    completed_at = ""
    for ev in events:
        event_type = str(ev.get("event_type") or "")
        ts = str(ev.get("timestamp") or "")
        if event_type == "run.start" and ts and not started_at:
            started_at = ts
        elif event_type == "run.completed" and ts and not completed_at:
            completed_at = ts
    return started_at, completed_at


def _normalize_event_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", ".")
    aliases = {
        "run.end": "run.completed",
        "run.complete": "run.completed",
        "phase.complete": "phase.end",
        "phase.completed": "phase.end",
        "task.complete": "task.end",
        "task.completed": "task.end",
        "validation.complete": "validation.end",
        "validation.completed": "validation.end",
    }
    return aliases.get(raw, raw)


def _event_category(event_type: str) -> str:
    head = event_type.split(".", 1)[0].strip().lower()
    if head in {"run", "phase", "task", "validation"}:
        return head
    return "unknown"


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


def _pick_int(source: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in source and source.get(key) is not None:
            return _to_int(source.get(key), default=0)
    return 0


def _coerce_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
