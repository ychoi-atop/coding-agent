"""Validation resolution, quality tracking, and dynamic concurrency helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Set

from .loop_utils import (
    DEFAULT_VALIDATOR_FALLBACK,
    _ordered_unique,
)

# ---------------------------------------------------------------------------
# Dynamic concurrency
# ---------------------------------------------------------------------------


def _dynamic_concurrency(
    base_max: int,
    client_usage: Dict[str, Any],
    total_remaining_tasks: int,
) -> int:
    """Adjust *max_parallel_tasks* based on remaining token budget.

    Returns *base_max* when no budget is configured.  Reduces to 1 when less
    than 25 % of the budget remains, and by 1 (min 1) when less than 50 %.
    """
    remaining = client_usage.get("remaining_tokens")
    max_total = client_usage.get("max_total_tokens")

    if remaining is None or max_total is None or max_total <= 0:
        return base_max  # no budget configured

    fraction = remaining / max_total
    if fraction < 0.25:
        return 1
    if fraction < 0.50:
        return max(1, base_max - 1)
    return base_max


# ---------------------------------------------------------------------------
# Gate profile resolution
# ---------------------------------------------------------------------------


def _resolve_gate_profile(
    quality_profile: Dict[str, Any] | None,
    gate_profile: str | None,
) -> Dict[str, Any]:
    if quality_profile is None or not gate_profile:
        out = dict(quality_profile) if quality_profile else {}
        out.setdefault("resolved_from", gate_profile or out.get("name", "balanced"))
        return out

    by_level = quality_profile.get("by_level")
    if not isinstance(by_level, dict):
        out = dict(quality_profile)
        out["name"] = gate_profile
        out["resolved_from"] = gate_profile
        return out

    overrides = by_level.get(gate_profile)
    if not isinstance(overrides, dict):
        out = dict(quality_profile)
        out.setdefault("name", out.get("name", gate_profile))
        out["resolved_from"] = gate_profile
        return out

    merged = {k: v for k, v in quality_profile.items() if k not in {"by_level", "name", "resolved_from"}}
    merged.update(overrides)
    merged["name"] = gate_profile
    merged["resolved_from"] = gate_profile
    return merged


# ---------------------------------------------------------------------------
# Validator resolution helpers
# ---------------------------------------------------------------------------


def _resolve_validators(focus: List[str] | None, validators_enabled: List[str]) -> List[str]:
    enabled = [v for v in validators_enabled if v in DEFAULT_VALIDATOR_FALLBACK]
    if focus:
        selected = [v for v in _ordered_unique(focus) if v in validators_enabled]
        if selected:
            return selected

    selected = [v for v in DEFAULT_VALIDATOR_FALLBACK if v in enabled]
    if selected:
        return selected
    return _ordered_unique(validators_enabled)


def _failure_signature(
    validation_rows: List[Dict[str, Any]],
    include_fingerprints: bool = True,
) -> tuple:
    """Create a failure signature from validation rows.

    When *include_fingerprints* is ``True`` (default) the signature embeds
    per-error fingerprint digests, making it sensitive to individual error
    identity rather than just ``(name, status, error_class)`` triples.
    """
    if not include_fingerprints:
        failers = [
            (row["name"], row.get("status", "unknown"), row.get("error_classification") or "")
            for row in validation_rows
            if not row["ok"]
        ]
        return tuple(failers)

    from .failure_analyzer import fingerprint_validation_row

    failers = []
    for row in validation_rows:
        if row["ok"]:
            continue
        fps = fingerprint_validation_row(row)
        digests = tuple(sorted(fp.digest for fp in fps))
        failers.append(
            (row["name"], row.get("status", "unknown"), digests)
        )
    return tuple(failers)


def _extract_fingerprint_digests(
    validation_rows: List[Dict[str, Any]],
) -> set[str]:
    """Extract all unique fingerprint digests from failed validation rows."""
    from .failure_analyzer import fingerprint_validation_row

    digests: set[str] = set()
    for row in validation_rows:
        if row.get("ok"):
            continue
        for fp in fingerprint_validation_row(row):
            digests.add(fp.digest)
    return digests


def _failed_validator_names(validation_rows: List[Dict[str, Any]]) -> List[str]:
    return [
        row["name"]
        for row in validation_rows
        if not row["ok"] and row.get("status") != "skipped_dependency"
    ]


def _has_skipped_dependency(validation_rows: List[Dict[str, Any]]) -> bool:
    return any(row.get("status") == "skipped_dependency" for row in validation_rows)


def _merge_validation_rows(
    previous: List[Dict[str, Any]],
    fresh: List[Dict[str, Any]],
    run_set: List[str],
) -> List[Dict[str, Any]]:
    by_name = {row["name"]: row for row in previous}
    fresh_by_name = {row["name"]: row for row in fresh}

    merged: List[Dict[str, Any]] = []
    for name in run_set:
        if name in fresh_by_name:
            merged.append(fresh_by_name[name])
        elif name in by_name:
            merged.append(by_name[name])

    existing = {row["name"] for row in merged}
    for row in fresh_by_name.values():
        if row["name"] not in existing:
            merged.append(row)

    return merged


def _validations_ok(validation_rows: List[Dict[str, Any]], soft_validators: set[str]) -> bool:
    blocking = [
        row
        for row in validation_rows
        if row["name"] not in soft_validators
        and row.get("status") != "skipped_dependency"
    ]
    return all(row["ok"] for row in blocking)


def _resolve_soft_fail(
    profile_section: Dict[str, Any] | None,
    explicit: List[str] | None,
    compact_key: str | None = None,
) -> Set[str]:
    if explicit is not None:
        return set(explicit)
    if not profile_section:
        return set()

    if compact_key and isinstance(profile_section, dict):
        compact_values = profile_section.get(compact_key)
        if compact_values is not None:
            profile_section = {"soft_fail": compact_values}

    values = profile_section.get("soft_fail")
    if isinstance(values, list):
        return set(values)
    return set()


def _resolve_repeat_failure_guard(quality_profile: Dict[str, Any] | None) -> Dict[str, Any]:
    defaults = {"enabled": True, "max_retries_before_targeted_fix": 1}
    if not isinstance(quality_profile, dict):
        return defaults

    escalation = quality_profile.get("escalation")
    if not isinstance(escalation, dict):
        return defaults

    guard = escalation.get("repeat_failure_guard")
    if not isinstance(guard, dict):
        return defaults

    enabled = guard.get("enabled", defaults["enabled"])
    max_retries = guard.get(
        "max_retries_before_targeted_fix",
        defaults["max_retries_before_targeted_fix"],
    )

    if not isinstance(enabled, bool):
        enabled = defaults["enabled"]
    if not isinstance(max_retries, int) or max_retries < 0:
        max_retries = defaults["max_retries_before_targeted_fix"]

    return {
        "enabled": enabled,
        "max_retries_before_targeted_fix": max_retries,
    }


# ---------------------------------------------------------------------------
# Quality row / metrics helpers
# ---------------------------------------------------------------------------


def _build_validator_counts(validation_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {"passed": 0, "failed": 0, "soft_fail": 0}
    for row in validation_rows:
        status = row.get("status", "failed")
        out[status] = out.get(status, 0) + 1
    return out


def _build_pass_map(validation_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        row["name"]: {
            "ok": bool(row["ok"]),
            "status": row.get("status", "failed"),
            "returncode": row.get("returncode", 1),
            "duration_ms": row.get("duration_ms", 0),
        }
        for row in validation_rows
    }


def _build_quality_row(
    task_id: str,
    attempt: int,
    run_set: List[str],
    validation_rows: List[Dict[str, Any]],
    duration_ms: int,
    soft_validators: Set[str],
    all_ok: bool,
    quality_notes: List[str] | None = None,
    validation_links: Dict[str, Any] | None = None,
    repair_pass: bool = False,
) -> Dict[str, Any]:
    blocked = [
        row
        for row in validation_rows
        if row["name"] not in soft_validators
        and row.get("status") != "skipped_dependency"
    ]
    hard_failures = sum(1 for row in blocked if not row["ok"])
    soft_failures = sum(1 for row in validation_rows if row["name"] in soft_validators and not row["ok"])

    return {
        "task_id": task_id,
        "attempt": attempt,
        "validator_focus": run_set,
        "duration_ms": duration_ms,
        "status": "passed" if all_ok else "failed",
        "repair_pass": repair_pass,
        "quality_notes": quality_notes or [],
        "validation_links": validation_links or {},
        "validator_counts": _build_validator_counts(validation_rows),
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "pass_fail_map": _build_pass_map(validation_rows),
        "validations": validation_rows,
    }


def _build_task_summary_rows(
    attempts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    trend = []
    for row in attempts:
        trend.append(
            {
                "attempt": row["attempt"],
                "status": row["status"],
                "hard_failures": row["hard_failures"],
                "soft_failures": row["soft_failures"],
                "duration_ms": row["duration_ms"],
            }
        )
    return trend


def _summarize_run(profile: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "quality_profile": profile,
        "project_type": plan["project"].get("type"),
        "quality_gate_profile": plan["project"].get("quality_gate_profile"),
        "generated_tasks": [t["id"] for t in plan.get("tasks", [])],
        "default_artifacts": plan["project"].get("default_artifacts", []),
    }


def _quality_metadata_from_changeset(
    changeset: Dict[str, Any],
    task_id: str,
    run_set: List[str],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    notes = changeset.get("quality_notes")
    links = changeset.get("validation_links")
    if isinstance(notes, list):
        out["quality_notes"] = notes
    if isinstance(links, dict):
        out["validation_links"] = links
    if "validation_links" not in out:
        out["validation_links"] = {
            "acceptance": [],
            "tasks": [task_id],
            "validators": run_set,
        }
    if "quality_notes" not in out:
        out["quality_notes"] = []
    handoff = changeset.get("handoff")
    if isinstance(handoff, dict):
        out["handoff"] = handoff
    return out
