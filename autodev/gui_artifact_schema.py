from __future__ import annotations

from typing import Any


SCHEMA_VERSION_KEYS = ("artifact_schema_version", "schema_version")
DEFAULT_FALLBACK_VERSION = "legacy-v0"


_V1_ALIASES = {"1", "1.0", "v1", "v1.0", DEFAULT_FALLBACK_VERSION}

KNOWN_SCHEMA_VERSIONS: dict[str, set[str]] = {
    "run_metadata": set(_V1_ALIASES),
    "checkpoint": set(_V1_ALIASES),
    "run_trace": set(_V1_ALIASES),
    "task_quality_index": set(_V1_ALIASES),
    "task_final_last_validation": set(_V1_ALIASES),
}


WarningPayload = dict[str, Any]
SchemaMarker = dict[str, Any]


def build_schema_marker(artifact: str, payload: Any) -> tuple[SchemaMarker, WarningPayload | None]:
    """Build a schema marker for an artifact payload.

    Behavior:
    - If artifact exposes a known schema version -> mark as known.
    - If version is absent -> use backward-compatible fallback marker (legacy-v0).
    - If version is present but unknown -> include warning and keep fallback effective version.
    """

    fallback_version = DEFAULT_FALLBACK_VERSION
    known_versions = KNOWN_SCHEMA_VERSIONS.get(artifact, {fallback_version})

    declared_version = _extract_declared_version(payload)
    if not declared_version:
        return (
            {
                "artifact": artifact,
                "declared_version": None,
                "effective_version": fallback_version,
                "known_version": True,
                "fallback_applied": True,
            },
            None,
        )

    known = declared_version in known_versions
    marker: SchemaMarker = {
        "artifact": artifact,
        "declared_version": declared_version,
        "effective_version": declared_version if known else fallback_version,
        "known_version": known,
        "fallback_applied": not known,
    }
    if known:
        return marker, None

    warning: WarningPayload = {
        "kind": "artifact_schema_warning",
        "code": "unknown_schema_version",
        "artifact": artifact,
        "declared_version": declared_version,
        "fallback_version": fallback_version,
        "message": (
            f"Unknown schema version '{declared_version}' for {artifact}; "
            f"falling back to '{fallback_version}' compatibility path"
        ),
    }
    return marker, warning


def summarize_schema_markers(artifacts: dict[str, Any]) -> tuple[dict[str, SchemaMarker], list[WarningPayload]]:
    markers: dict[str, SchemaMarker] = {}
    warnings: list[WarningPayload] = []

    for artifact, payload in artifacts.items():
        marker, warning = build_schema_marker(artifact, payload)
        markers[artifact] = marker
        if warning is not None:
            warnings.append(warning)

    return markers, warnings


def _extract_declared_version(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    for key in SCHEMA_VERSION_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return None
