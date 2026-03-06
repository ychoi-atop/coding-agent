from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def build_run_control_fix_hints(
    *,
    action: str,
    error: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None = None,
    runs_root: Path | None = None,
) -> list[str]:
    """Return deterministic, non-sensitive fix hints for run-control failures."""

    err = error if isinstance(error, Mapping) else {}
    code = str(err.get("code") or "").strip().lower()
    message = str(err.get("message") or "").strip().lower()
    hints: list[str] = []

    def add(text: str) -> None:
        val = str(text).strip()
        if val and val not in hints:
            hints.append(val)

    if code == "forbidden_role":
        allowed = err.get("allowed_roles")
        if isinstance(allowed, list):
            allowed_roles = ", ".join(sorted({str(v).strip() for v in allowed if str(v).strip()}))
            if allowed_roles:
                add(f"Use a mutating role ({allowed_roles}) or switch to local-simple mode.")
        add("If you're using token/session auth, verify the policy allows this action.")

    if code in {"missing_prd", "invalid_prd"}:
        add("Set PRD to an existing file path (for example: examples/PRD.md).")

    if code in {"missing_out", "invalid_out"}:
        add("Set Out to a directory path (not a file).")

    if code == "resume_out_missing":
        add("For Resume, set Out to an existing run directory that contains .autodev/.")

    if code == "missing_profile":
        add("Set Profile (for local laptop workflow, try local_simple).")

    if code == "invalid_config":
        add("Set Config to an existing file path, or clear the field.")

    if code in {"missing_process_id", "not_found"} and "process_id" in message:
        add("Provide a valid process_id from the latest Start/Retry response.")

    if code in {"missing_retry_target", "not_found"} and "run_id" in message:
        add("For Retry, provide process_id or run_id.")

    if code == "invalid_graceful_timeout":
        add("Set graceful_timeout_sec to a number greater than 0 (for example 2.0).")

    if code == "launch_failed":
        add("Confirm the autodev CLI is installed and executable in this environment.")

    if code == "invalid_payload":
        if "missing '.autodev/'" in message:
            add("Select a run directory that includes .autodev/ metadata.")
        if "missing '.autodev/checkpoint.json'" in message:
            add("Resume requires .autodev/checkpoint.json in the selected run directory.")
        if "missing '.autodev/run_metadata.json'" in message:
            add("Resume requires .autodev/run_metadata.json in the selected run directory.")
        if "status is terminal" in message or "appears finalized" in message:
            add("This run is finalized; use Retry (or Start a new run) instead of Resume.")
        if "resumable markers" in message:
            add("Checkpoint is incomplete; pick a run that has completed_task_ids or failed_task_id.")

    # Validator signal hint for resume/retry flows when artifacts are available.
    if action in {"resume", "retry"}:
        validator_hint = _validator_failure_hint(payload=payload, runs_root=runs_root)
        if validator_hint:
            add(validator_hint)

    return hints[:4]


def _validator_failure_hint(*, payload: Mapping[str, Any] | None, runs_root: Path | None) -> str:
    run_dir = _resolve_run_dir(payload=payload, runs_root=runs_root)
    if not run_dir:
        return ""

    validation_path = run_dir / ".autodev" / "task_final_last_validation.json"
    if not validation_path.is_file():
        return ""

    rows = _read_validation_rows(validation_path)
    if not rows:
        return ""

    failed = []
    for row in rows:
        status = _normalize_status(row.get("status"), row.get("ok"))
        if status in {"failed", "soft_fail"}:
            name = str(row.get("name") or row.get("validator") or "").strip()
            if name and name not in failed:
                failed.append(name)

    if not failed:
        return ""

    sample = ", ".join(failed[:3])
    if len(failed) == 1:
        return f"Recent validator failure: {sample}. Check Validation tab before retry/resume."
    return f"Recent validator failures: {sample}. Check Validation tab before retry/resume."


def _resolve_run_dir(*, payload: Mapping[str, Any] | None, runs_root: Path | None) -> Path | None:
    if not isinstance(payload, Mapping):
        return None

    out = str(payload.get("out") or "").strip()
    if out:
        out_path = Path(out).expanduser()
        if out_path.is_dir():
            return out_path.resolve()

    run_id = str(payload.get("run_id") or "").strip()
    if run_id and runs_root is not None:
        candidate = runs_root / run_id
        if candidate.is_dir():
            return candidate.resolve()

    return None


def _read_validation_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict):
        return []

    for key in ("validation", "validations", "results", "rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _normalize_status(status: Any, ok: Any) -> str:
    raw = str(status or "").strip().lower()
    if raw in {"pass", "passed", "ok", "success", "succeeded", "soft_pass"}:
        return "passed"
    if raw in {"soft_fail", "soft-fail", "softfail", "warn", "warning"}:
        return "soft_fail"
    if raw in {"fail", "failed", "error", "errored"}:
        return "failed"
    if ok is True:
        return "passed"
    if ok is False:
        return "failed"
    return "unknown"
