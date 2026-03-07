from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

GateName = Literal["tests", "security", "performance"]
NormalizedStatus = Literal["passed", "failed", "unknown"]


_STATUS_ALIASES = {
    "pass": "passed",
    "passed": "passed",
    "ok": "passed",
    "success": "passed",
    "fail": "failed",
    "failed": "failed",
    "error": "failed",
    "timeout": "failed",
    "skipped": "unknown",
    "not_run": "unknown",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class NormalizedValidationSignal:
    name: str
    status: NormalizedStatus
    ok: bool | None
    diagnostics: dict[str, Any]
    stdout: str
    stderr: str
    signal_source: str


@dataclass(frozen=True)
class GateFailureReason:
    type: Literal["quality_gate_failed"]
    taxonomy_version: Literal["av2-003"]
    gate: GateName
    category: str
    code: str
    severity: Literal["blocking"]
    retryable: bool
    signal_source: str
    message: str
    threshold: dict[str, Any]
    observed: dict[str, Any]


def normalize_validation_signals(last_validation: Any) -> list[NormalizedValidationSignal]:
    if not isinstance(last_validation, list):
        return []

    rows: list[NormalizedValidationSignal] = []
    for row in last_validation:
        if not isinstance(row, dict):
            continue

        name = _normalize_name(row.get("name"))
        status = _normalize_status(row)
        diagnostics = row.get("diagnostics") if isinstance(row.get("diagnostics"), dict) else {}
        ok_value = row.get("ok")
        ok = ok_value if isinstance(ok_value, bool) else None

        rows.append(
            NormalizedValidationSignal(
                name=name,
                status=status,
                ok=ok,
                diagnostics=diagnostics,
                stdout=str(row.get("stdout") or ""),
                stderr=str(row.get("stderr") or ""),
                signal_source="final_validation",
            )
        )

    return rows


def make_gate_failure_reason(
    *,
    gate: GateName,
    code: str,
    message: str,
    signal_source: str,
    threshold: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    reason = GateFailureReason(
        type="quality_gate_failed",
        taxonomy_version="av2-003",
        gate=gate,
        category=_gate_category(gate),
        code=code,
        severity="blocking",
        retryable=True,
        signal_source=signal_source,
        message=message,
        threshold=threshold,
        observed=observed,
    )
    return asdict(reason)


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"py_test", "tests", "unit_tests"}:
        return "pytest"
    if text in {"pipaudit", "pip_audit"}:
        return "pip_audit"
    return text


def _normalize_status(row: dict[str, Any]) -> NormalizedStatus:
    ok = row.get("ok")
    if isinstance(ok, bool):
        return "passed" if ok else "failed"

    raw_status = str(row.get("status") or "").strip().lower().replace("-", "_")
    normalized = _STATUS_ALIASES.get(raw_status)
    if normalized is not None:
        return normalized

    returncode = row.get("returncode")
    if isinstance(returncode, int):
        return "passed" if returncode == 0 else "failed"

    return "unknown"


def _gate_category(gate: GateName) -> str:
    if gate == "tests":
        return "reliability"
    if gate == "security":
        return "security"
    return "performance"
