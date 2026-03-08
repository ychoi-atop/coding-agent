from __future__ import annotations

from dataclasses import dataclass
from typing import Any

AUTONOMOUS_EVIDENCE_SCHEMA_VERSION = "av3-002-v1"
AUTONOMOUS_EVIDENCE_LEGACY_VERSION = "av2-legacy"


@dataclass
class SchemaDiagnostic:
    level: str
    path: str
    message: str


def _is_non_empty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _lookup_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _validate_required_fields(payload: dict[str, Any], *, path: str, required_fields: tuple[str, ...]) -> list[SchemaDiagnostic]:
    diagnostics: list[SchemaDiagnostic] = []
    for field in required_fields:
        value = _lookup_path(payload, field)
        if not _is_non_empty(value):
            diagnostics.append(
                SchemaDiagnostic(
                    level="error",
                    path=path,
                    message=f"missing required field: {path}.{field}",
                )
            )
    return diagnostics


SCHEMA_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "report": ("mode", "ok", "run_id", "preflight.status", "guard_decision.reason_code"),
    "gate": ("attempts",),
    "strategy": ("attempts",),
    "guard": ("latest.reason_code", "decisions"),
    "preflight": ("status", "ok"),
    "summary_snapshot": ("preflight_status", "gate_counts.total", "guard_decision.reason_code"),
}


def validate_schema_payload(
    *,
    name: str,
    payload: dict[str, Any],
    path: str,
    tolerant_legacy: bool,
) -> tuple[str, list[SchemaDiagnostic]]:
    diagnostics: list[SchemaDiagnostic] = []

    declared = payload.get("schema_version")
    if declared == AUTONOMOUS_EVIDENCE_SCHEMA_VERSION:
        diagnostics.extend(
            _validate_required_fields(
                payload,
                path=path,
                required_fields=SCHEMA_REQUIRED_FIELDS.get(name, ()),
            )
        )
        return AUTONOMOUS_EVIDENCE_SCHEMA_VERSION, diagnostics

    if declared is None:
        if tolerant_legacy:
            diagnostics.append(
                SchemaDiagnostic(
                    level="warning",
                    path=path,
                    message=(
                        f"legacy compatibility mode: {path} has no schema_version; "
                        f"assuming {AUTONOMOUS_EVIDENCE_LEGACY_VERSION}"
                    ),
                )
            )
            # Minimal legacy validation still applies.
            diagnostics.extend(
                _validate_required_fields(
                    payload,
                    path=path,
                    required_fields=SCHEMA_REQUIRED_FIELDS.get(name, ()),
                )
            )
            return AUTONOMOUS_EVIDENCE_LEGACY_VERSION, diagnostics

        diagnostics.append(
            SchemaDiagnostic(
                level="error",
                path=path,
                message=f"{path}.schema_version is required",
            )
        )
        return AUTONOMOUS_EVIDENCE_LEGACY_VERSION, diagnostics

    if tolerant_legacy:
        diagnostics.append(
            SchemaDiagnostic(
                level="warning",
                path=path,
                message=(
                    f"unknown schema_version '{declared}' at {path}; "
                    f"falling back to {AUTONOMOUS_EVIDENCE_LEGACY_VERSION}"
                ),
            )
        )
        diagnostics.extend(
            _validate_required_fields(
                payload,
                path=path,
                required_fields=SCHEMA_REQUIRED_FIELDS.get(name, ()),
            )
        )
        return AUTONOMOUS_EVIDENCE_LEGACY_VERSION, diagnostics

    diagnostics.append(
        SchemaDiagnostic(
            level="error",
            path=path,
            message=f"unsupported schema_version '{declared}' at {path}",
        )
    )
    return AUTONOMOUS_EVIDENCE_LEGACY_VERSION, diagnostics
