from __future__ import annotations

from typing import Any

_NORMALIZED = {"ok", "failed", "running", "unknown"}

_STATUS_ALIASES: dict[str, str] = {
    # ok
    "ok": "ok",
    "pass": "ok",
    "passed": "ok",
    "success": "ok",
    "succeeded": "ok",
    "completed": "ok",
    "complete": "ok",
    # failed
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "errored": "failed",
    "blocked": "failed",
    "timeout": "failed",
    "cancelled": "failed",
    "canceled": "failed",
    # running
    "running": "running",
    "in_progress": "running",
    "in-progress": "running",
    "pending": "running",
    "queued": "running",
    "resuming": "running",
    "started": "running",
    "partial": "running",
    # unknown
    "unknown": "unknown",
}


def normalize_run_status(
    *,
    metadata: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    quality_index: dict[str, Any] | None = None,
    default: str = "unknown",
) -> str:
    """Normalize run status into one of: ok, failed, running, unknown.

    Precedence:
    1) metadata.result_ok (authoritative boolean completion signal)
    2) quality_index.final.status
    3) checkpoint.status
    4) provided default (normalized)
    """

    if isinstance(metadata, dict):
        result_ok = metadata.get("result_ok")
        if result_ok is True:
            return "ok"
        if result_ok is False:
            return "failed"

    if isinstance(quality_index, dict):
        final = quality_index.get("final")
        if isinstance(final, dict):
            normalized = _normalize_alias(final.get("status"))
            if normalized != "unknown":
                return normalized

    if isinstance(checkpoint, dict):
        normalized = _normalize_alias(checkpoint.get("status"))
        if normalized != "unknown":
            return normalized

    fallback = _normalize_alias(default)
    return fallback if fallback in _NORMALIZED else "unknown"


def _normalize_alias(value: Any) -> str:
    if isinstance(value, bool):
        return "ok" if value else "failed"
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if not text:
        return "unknown"
    return _STATUS_ALIASES.get(text, "unknown")
