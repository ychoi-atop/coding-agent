#!/usr/bin/env python3
"""Apply canonical autonomous-wave events to status docs.

AV4-001 scope:
- map canonical status events to docs status transitions
- keep updates idempotent and easy to run manually as fallback

AV4-002 scope:
- add no-write drift-check mode for CI/quality gates
- allow CI validation to fail when docs diverge from canonical hook output

AV4-003 scope:
- introduce explicit status-hook event registry metadata
- schema-validate registry entries with fail-fast diagnostics
- enforce registry validation in runtime and CI paths

AV4-004 scope:
- expand canonical event registry beyond kickoff with practical lifecycle events
- keep apply/drift behavior deterministic across event-to-event transitions

AV4-005 scope:
- append-only status-hook audit log (apply/drift-check/replay)
- replay prior audit entry safely (dry-run default; opt-in apply)

AV4-006 scope:
- write lockfile/concurrency guard for apply + replay --apply
- stale lock policy with explicit force override + lock diagnostics in audit trail

AV4-010 scope:
- extend rollup flow to include AV4 closure ledger status line
- allow one scripted event flow to sync status/plan/backlog/closure docs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CanonicalEventSpec:
    mode: str
    scope: str
    state: str
    av4_snapshot: str
    plan_title: str
    plan_av4_snapshot: str
    backlog_title: str
    backlog_av4_snapshot: str
    closure_status: str


@dataclass(frozen=True)
class EventRegistryEntry:
    event_id: str
    description: str
    expected_doc_transitions: tuple[str, ...]
    spec: CanonicalEventSpec


STATUS_TIMESTAMP_PREFIX = "Status timestamp: "
STATUS_MODE_PREFIX = "- **Mode:** "
STATUS_SCOPE_PREFIX = "- **Scope:** "
STATUS_STATE_PREFIX = "- **State:** "
STATUS_AV4_PREFIX = "- **AV4:** "
PLAN_TITLE_PREFIX = "# PLAN — Next Wave ("
PLAN_AV4_PREFIX = "- AV4 "
BACKLOG_TITLE_PREFIX = "# BACKLOG — Next Wave ("
BACKLOG_AV4_PREFIX = "- AV4 "
CLOSURE_STATUS_PREFIX = "Status: "
DEFAULT_AUDIT_LOG_RELATIVE = "artifacts/status-hooks/status-hook-audit.jsonl"
DEFAULT_LOCKFILE_RELATIVE = "artifacts/status-hooks/status-hook-write.lock"
DEFAULT_LOCK_STALE_SECONDS = 900

EXPECTED_DOC_TRANSITIONS = (
    "STATUS_BOARD_CURRENT.md",
    "PLAN_NEXT_WEEK.md",
    "BACKLOG_NEXT_WEEK.md",
    "AUTONOMOUS_V4_WAVE_CLOSURE.md",
)


EVENT_REGISTRY: tuple[EventRegistryEntry, ...] = (
    EventRegistryEntry(
        event_id="av4.kickoff.started",
        description="Initialize AV4 kickoff docs baseline across status/plan/backlog/closure.",
        expected_doc_transitions=EXPECTED_DOC_TRANSITIONS,
        spec=CanonicalEventSpec(
            mode="AV4 Kickoff",
            scope="AV4 wave planning + kickoff execution start",
            state="AV3 closed on `main`; AV4 kickoff package started",
            av4_snapshot="🚧 Kickoff started (plan + backlog published)",
            plan_title="# PLAN — Next Wave (AV4 Kickoff Active)",
            plan_av4_snapshot="- AV4 kickoff package is now active (`docs/AUTONOMOUS_V4_WAVE_PLAN.md`, `docs/AUTONOMOUS_V4_BACKLOG.md`).",
            backlog_title="# BACKLOG — Next Wave (AV4 Kickoff Queue)",
            backlog_av4_snapshot="- AV4 kickoff: 🚧 started",
            closure_status="🚧 Open (kickoff active; closure pending)",
        ),
    ),
    EventRegistryEntry(
        event_id="av4.execution.in_progress",
        description="Reflect active AV4 implementation and validation in status/plan/backlog/closure docs.",
        expected_doc_transitions=EXPECTED_DOC_TRANSITIONS,
        spec=CanonicalEventSpec(
            mode="AV4 Execution",
            scope="AV4 delivery in progress across prioritized ticket slices",
            state="AV4 kickoff completed; active implementation and validation underway",
            av4_snapshot="🏗️ Execution in progress (P0 slices actively shipping)",
            plan_title="# PLAN — Next Wave (AV4 Execution In Progress)",
            plan_av4_snapshot="- AV4 execution is in progress (P0 tickets moving through implementation + validation).",
            backlog_title="# BACKLOG — Next Wave (AV4 Active Delivery Queue)",
            backlog_av4_snapshot="- AV4 execution: 🏗️ in progress",
            closure_status="🏗️ Open (execution in progress)",
        ),
    ),
    EventRegistryEntry(
        event_id="av4.stabilization.started",
        description="Mark AV4 as feature-complete and shift tracking to stabilization evidence.",
        expected_doc_transitions=EXPECTED_DOC_TRANSITIONS,
        spec=CanonicalEventSpec(
            mode="AV4 Stabilization",
            scope="AV4 feature-complete freeze and stabilization checks",
            state="AV4 implementation complete; stabilization and release verification running",
            av4_snapshot="🧪 Stabilization started (smoke + release gates in focus)",
            plan_title="# PLAN — Next Wave (AV4 Stabilization Active)",
            plan_av4_snapshot="- AV4 stabilization is active (feature-complete; focus shifted to reliability evidence).",
            backlog_title="# BACKLOG — Next Wave (AV4 Stabilization Queue)",
            backlog_av4_snapshot="- AV4 stabilization: 🧪 started",
            closure_status="🧪 Open (stabilization active)",
        ),
    ),
    EventRegistryEntry(
        event_id="av4.closed",
        description="Close AV4 wave tracking and transition docs to post-wave planning mode.",
        expected_doc_transitions=EXPECTED_DOC_TRANSITIONS,
        spec=CanonicalEventSpec(
            mode="AV4 Closed",
            scope="AV4 wave closure and handoff to next wave planning",
            state="AV4 delivery closed on `main`; next-wave kickoff prep open",
            av4_snapshot="✅ Closed (execution + stabilization complete)",
            plan_title="# PLAN — Next Wave (Post-AV4 Planning)",
            plan_av4_snapshot="- AV4 is closed on `main`; planning focus shifts to the next wave package.",
            backlog_title="# BACKLOG — Next Wave (Post-AV4 Intake Queue)",
            backlog_av4_snapshot="- AV4 closure: ✅ complete",
            closure_status="✅ Closed on `main`",
        ),
    ),
)


def _build_event_map_from_registry(registry: Sequence[EventRegistryEntry | Mapping[str, Any]]) -> dict[str, CanonicalEventSpec]:
    errors = validate_event_registry(registry)
    if errors:
        formatted = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"invalid status-hook event registry:\n{formatted}")

    event_map: dict[str, CanonicalEventSpec] = {}
    for entry in registry:
        if isinstance(entry, EventRegistryEntry):
            event_map[entry.event_id] = entry.spec
            continue
        # Defensive fallback for mapping-typed entries in tests/validation tooling.
        event_id = str(entry["event_id"])
        spec = entry["spec"]
        if isinstance(spec, CanonicalEventSpec):
            event_map[event_id] = spec
        else:
            event_map[event_id] = CanonicalEventSpec(**spec)
    return event_map


def _kst_timestamp() -> str:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.strftime("%Y-%m-%d %H:%M KST (Asia/Seoul)")


def _replace_once(content: str, old: str, new: str, *, file_path: Path) -> str:
    if old not in content:
        raise ValueError(f"expected snippet not found in {file_path}: {old}")
    return content.replace(old, new, 1)


def _replace_line_by_prefix(content: str, prefix: str, replacement: str, *, file_path: Path) -> str:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement
            break
    else:
        raise ValueError(f"expected line prefix not found in {file_path}: {prefix}")

    trailing_newline = "\n" if content.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline


def _extract_status_timestamp(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith(STATUS_TIMESTAMP_PREFIX):
            return line.removeprefix(STATUS_TIMESTAMP_PREFIX)
    return None


def _render_status_board(content: str, spec: CanonicalEventSpec, *, file_path: Path, timestamp: str) -> str:
    updated = content
    updated = _replace_line_by_prefix(updated, STATUS_MODE_PREFIX, f"{STATUS_MODE_PREFIX}{spec.mode}", file_path=file_path)
    updated = _replace_line_by_prefix(updated, STATUS_SCOPE_PREFIX, f"{STATUS_SCOPE_PREFIX}{spec.scope}", file_path=file_path)
    updated = _replace_line_by_prefix(updated, STATUS_STATE_PREFIX, f"{STATUS_STATE_PREFIX}{spec.state}", file_path=file_path)
    updated = _replace_line_by_prefix(updated, STATUS_AV4_PREFIX, f"{STATUS_AV4_PREFIX}{spec.av4_snapshot}", file_path=file_path)

    for line in updated.splitlines():
        if line.startswith(STATUS_TIMESTAMP_PREFIX):
            updated = _replace_once(
                updated,
                line,
                f"{STATUS_TIMESTAMP_PREFIX}{timestamp}",
                file_path=file_path,
            )
            break

    return updated


def _render_plan(content: str, spec: CanonicalEventSpec, *, file_path: Path) -> str:
    updated = _replace_line_by_prefix(content, PLAN_TITLE_PREFIX, spec.plan_title, file_path=file_path)
    updated = _replace_line_by_prefix(updated, PLAN_AV4_PREFIX, spec.plan_av4_snapshot, file_path=file_path)
    return updated


def _render_backlog(content: str, spec: CanonicalEventSpec, *, file_path: Path) -> str:
    updated = _replace_line_by_prefix(content, BACKLOG_TITLE_PREFIX, spec.backlog_title, file_path=file_path)
    updated = _replace_line_by_prefix(updated, BACKLOG_AV4_PREFIX, spec.backlog_av4_snapshot, file_path=file_path)
    return updated


def _render_closure(content: str, spec: CanonicalEventSpec, *, file_path: Path) -> str:
    return _replace_line_by_prefix(content, CLOSURE_STATUS_PREFIX, f"{CLOSURE_STATUS_PREFIX}{spec.closure_status}", file_path=file_path)


def _entry_to_mapping(entry: EventRegistryEntry | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(entry, EventRegistryEntry):
        return {
            "event_id": entry.event_id,
            "description": entry.description,
            "expected_doc_transitions": entry.expected_doc_transitions,
            "spec": entry.spec,
        }
    return entry


def _validate_spec(spec: Any, *, label: str) -> list[str]:
    errors: list[str] = []
    if isinstance(spec, CanonicalEventSpec):
        return errors
    if not isinstance(spec, Mapping):
        errors.append(f"{label}: spec must be CanonicalEventSpec or mapping")
        return errors

    required_fields = (
        "mode",
        "scope",
        "state",
        "av4_snapshot",
        "plan_title",
        "plan_av4_snapshot",
        "backlog_title",
        "backlog_av4_snapshot",
        "closure_status",
    )
    for field_name in required_fields:
        value = spec.get(field_name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: spec.{field_name} must be a non-empty string")
    return errors


def validate_event_registry(registry: Sequence[EventRegistryEntry | Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_event_ids: set[str] = set()

    if not registry:
        return ["registry must contain at least one event entry"]

    expected_transitions = set(EXPECTED_DOC_TRANSITIONS)

    for index, raw_entry in enumerate(registry):
        label = f"entry[{index}]"
        entry = _entry_to_mapping(raw_entry)

        event_id = entry.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            errors.append(f"{label}: event_id must be a non-empty string")
            normalized_event_id = "<missing>"
        else:
            normalized_event_id = event_id.strip()
            if normalized_event_id in seen_event_ids:
                errors.append(f"{label}: duplicate event_id '{normalized_event_id}'")
            else:
                seen_event_ids.add(normalized_event_id)

        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{label} ({normalized_event_id}): description must be a non-empty string")

        transitions = entry.get("expected_doc_transitions")
        if not isinstance(transitions, (list, tuple)) or not transitions:
            errors.append(f"{label} ({normalized_event_id}): expected_doc_transitions must be a non-empty list/tuple")
        else:
            normalized_transitions = [t for t in transitions if isinstance(t, str) and t.strip()]
            if len(normalized_transitions) != len(transitions):
                errors.append(f"{label} ({normalized_event_id}): expected_doc_transitions must contain only non-empty strings")
            if set(normalized_transitions) != expected_transitions:
                expected = ", ".join(sorted(expected_transitions))
                found = ", ".join(sorted(normalized_transitions))
                errors.append(
                    f"{label} ({normalized_event_id}): expected_doc_transitions must exactly match {{{expected}}}; found {{{found}}}"
                )

        errors.extend(_validate_spec(entry.get("spec"), label=f"{label} ({normalized_event_id})"))

    return errors


# Kept for backwards compatibility with existing tests/scripts importing this symbol.
CANONICAL_EVENT_MAP: dict[str, CanonicalEventSpec] = _build_event_map_from_registry(EVENT_REGISTRY)


def _resolve_spec(event: str) -> CanonicalEventSpec:
    spec = CANONICAL_EVENT_MAP.get(event)
    if spec is None:
        known = ", ".join(sorted(CANONICAL_EVENT_MAP))
        raise ValueError(f"unknown event '{event}'. Known events: {known}")
    return spec


def _compute_expected_contents(
    event: str,
    *,
    docs_root: Path,
    timestamp: str | None,
    preserve_existing_timestamp: bool,
) -> tuple[dict[Path, str], dict[Path, str]]:
    spec = _resolve_spec(event)

    status_path = docs_root / "STATUS_BOARD_CURRENT.md"
    plan_path = docs_root / "PLAN_NEXT_WEEK.md"
    backlog_path = docs_root / "BACKLOG_NEXT_WEEK.md"
    closure_path = docs_root / "AUTONOMOUS_V4_WAVE_CLOSURE.md"

    originals = {
        status_path: status_path.read_text(encoding="utf-8"),
        plan_path: plan_path.read_text(encoding="utf-8"),
        backlog_path: backlog_path.read_text(encoding="utf-8"),
        closure_path: closure_path.read_text(encoding="utf-8"),
    }

    if timestamp is not None:
        resolved_timestamp = timestamp
    elif preserve_existing_timestamp:
        resolved_timestamp = _extract_status_timestamp(originals[status_path]) or _kst_timestamp()
    else:
        resolved_timestamp = _kst_timestamp()

    expected = {
        status_path: _render_status_board(originals[status_path], spec, file_path=status_path, timestamp=resolved_timestamp),
        plan_path: _render_plan(originals[plan_path], spec, file_path=plan_path),
        backlog_path: _render_backlog(originals[backlog_path], spec, file_path=backlog_path),
        closure_path: _render_closure(originals[closure_path], spec, file_path=closure_path),
    }
    return originals, expected


def apply_event(event: str, *, docs_root: Path, timestamp: str | None = None) -> list[Path]:
    originals, expected = _compute_expected_contents(
        event,
        docs_root=docs_root,
        timestamp=timestamp,
        preserve_existing_timestamp=False,
    )

    changed: list[Path] = []
    for path, expected_content in expected.items():
        if expected_content != originals[path]:
            path.write_text(expected_content, encoding="utf-8")
            changed.append(path)
    return changed


def drift_check_event(event: str, *, docs_root: Path, timestamp: str | None = None) -> list[Path]:
    spec = _resolve_spec(event)

    status_path = docs_root / "STATUS_BOARD_CURRENT.md"
    plan_path = docs_root / "PLAN_NEXT_WEEK.md"
    backlog_path = docs_root / "BACKLOG_NEXT_WEEK.md"
    closure_path = docs_root / "AUTONOMOUS_V4_WAVE_CLOSURE.md"

    originals = {
        status_path: status_path.read_text(encoding="utf-8"),
        plan_path: plan_path.read_text(encoding="utf-8"),
        backlog_path: backlog_path.read_text(encoding="utf-8"),
        closure_path: closure_path.read_text(encoding="utf-8"),
    }

    resolved_timestamp = timestamp or _extract_status_timestamp(originals[status_path]) or _kst_timestamp()

    drifted: list[Path] = []

    try:
        status_expected = _render_status_board(
            originals[status_path],
            spec,
            file_path=status_path,
            timestamp=resolved_timestamp,
        )
        if status_expected != originals[status_path]:
            drifted.append(status_path)
    except ValueError:
        drifted.append(status_path)

    try:
        plan_expected = _render_plan(originals[plan_path], spec, file_path=plan_path)
        if plan_expected != originals[plan_path]:
            drifted.append(plan_path)
    except ValueError:
        drifted.append(plan_path)

    try:
        backlog_expected = _render_backlog(originals[backlog_path], spec, file_path=backlog_path)
        if backlog_expected != originals[backlog_path]:
            drifted.append(backlog_path)
    except ValueError:
        drifted.append(backlog_path)

    try:
        closure_expected = _render_closure(originals[closure_path], spec, file_path=closure_path)
        if closure_expected != originals[closure_path]:
            drifted.append(closure_path)
    except ValueError:
        drifted.append(closure_path)

    return drifted


def detect_matching_event(*, docs_root: Path) -> str:
    status_path = docs_root / "STATUS_BOARD_CURRENT.md"
    status_text = status_path.read_text(encoding="utf-8")

    current_mode: str | None = None
    for line in status_text.splitlines():
        if line.startswith(STATUS_MODE_PREFIX):
            current_mode = line.removeprefix(STATUS_MODE_PREFIX).strip()
            break

    if not current_mode:
        raise ValueError(f"could not detect mode from {status_path}")

    matches = [event_id for event_id, spec in sorted(CANONICAL_EVENT_MAP.items()) if spec.mode == current_mode]
    if len(matches) == 1:
        return matches[0]

    known_modes = ", ".join(sorted({spec.mode for spec in CANONICAL_EVENT_MAP.values()}))
    if not matches:
        raise ValueError(
            f"status mode '{current_mode}' is not mapped to a canonical event. Known modes: {known_modes}"
        )

    detected = ", ".join(matches)
    raise ValueError(f"ambiguous mode mapping for '{current_mode}': {detected}")


def _default_audit_log_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_AUDIT_LOG_RELATIVE


def _default_lockfile_path() -> Path:
    return Path(__file__).resolve().parents[1] / DEFAULT_LOCKFILE_RELATIVE


class StatusHookLockError(RuntimeError):
    def __init__(self, message: str, diagnostics: Mapping[str, Any]):
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


@dataclass(frozen=True)
class WriteLockHandle:
    path: Path
    token: str


def _read_lock_metadata(lockfile_path: Path) -> dict[str, Any]:
    try:
        raw = lockfile_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw.strip()}

    if isinstance(value, dict):
        return value
    return {"value": value}


def _acquire_write_lock(
    *,
    lockfile_path: Path,
    stale_after_seconds: int,
    force_override: bool,
) -> tuple[WriteLockHandle, Mapping[str, Any]]:
    if stale_after_seconds < 1:
        raise ValueError("--lock-stale-seconds must be >= 1")

    lockfile_path.parent.mkdir(parents=True, exist_ok=True)

    token = uuid4().hex
    now_epoch = time.time()
    payload = {
        "lock_token": token,
        "pid": os.getpid(),
        "acquired_at_epoch": now_epoch,
        "acquired_at_kst": _kst_timestamp(),
        "argv": sys.argv,
    }

    def _try_create() -> bool:
        try:
            fd = os.open(str(lockfile_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return True

    if not _try_create():
        existing = _read_lock_metadata(lockfile_path)
        acquired_at_epoch = existing.get("acquired_at_epoch") if isinstance(existing, Mapping) else None
        try:
            acquired_epoch_value = float(acquired_at_epoch)
        except (TypeError, ValueError):
            acquired_epoch_value = None

        if acquired_epoch_value is None:
            try:
                acquired_epoch_value = lockfile_path.stat().st_mtime
            except OSError:
                acquired_epoch_value = now_epoch

        age_seconds = max(0.0, now_epoch - acquired_epoch_value)
        stale = age_seconds >= stale_after_seconds
        base_diag = {
            "status": "blocked",
            "lock_file": str(lockfile_path),
            "stale": stale,
            "age_seconds": round(age_seconds, 3),
            "stale_after_seconds": stale_after_seconds,
            "force_override": force_override,
            "existing_lock": dict(existing) if isinstance(existing, Mapping) else {},
        }

        if not stale:
            raise StatusHookLockError(
                f"status-hook write lock is already held: {lockfile_path}",
                diagnostics=base_diag,
            )

        if not force_override:
            raise StatusHookLockError(
                (
                    f"status-hook write lock appears stale (age={int(age_seconds)}s >= {stale_after_seconds}s): "
                    f"{lockfile_path}. Re-run with --force-lock to override."
                ),
                diagnostics=base_diag,
            )

        try:
            lockfile_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            base_diag = dict(base_diag)
            base_diag["override_error"] = str(exc)
            raise StatusHookLockError(
                f"failed to override stale status-hook lock: {exc}",
                diagnostics=base_diag,
            ) from exc

        if not _try_create():
            race_diag = dict(base_diag)
            race_diag["status"] = "blocked_after_stale_override_race"
            race_diag["stale_override_attempted"] = True
            race_diag["existing_lock"] = _read_lock_metadata(lockfile_path)
            raise StatusHookLockError(
                f"status-hook write lock was acquired by another process during stale override: {lockfile_path}",
                diagnostics=race_diag,
            )

        acquired_diag = {
            "status": "acquired",
            "lock_file": str(lockfile_path),
            "token": token,
            "stale_override": True,
            "stale_after_seconds": stale_after_seconds,
            "replaced_lock": dict(existing) if isinstance(existing, Mapping) else {},
        }
        return WriteLockHandle(path=lockfile_path, token=token), acquired_diag

    acquired_diag = {
        "status": "acquired",
        "lock_file": str(lockfile_path),
        "token": token,
        "stale_override": False,
        "stale_after_seconds": stale_after_seconds,
    }
    return WriteLockHandle(path=lockfile_path, token=token), acquired_diag


def _release_write_lock(handle: WriteLockHandle) -> Mapping[str, Any]:
    diag: dict[str, Any] = {
        "status": "released",
        "lock_file": str(handle.path),
        "token": handle.token,
    }

    if not handle.path.exists():
        diag["status"] = "already_missing"
        return diag

    current = _read_lock_metadata(handle.path)
    current_token = current.get("lock_token") if isinstance(current, Mapping) else None
    if current_token and current_token != handle.token:
        diag["status"] = "not_owner"
        diag["current_lock"] = dict(current) if isinstance(current, Mapping) else {}
        return diag

    try:
        handle.path.unlink()
    except FileNotFoundError:
        diag["status"] = "already_missing"
    except OSError as exc:
        diag["status"] = "release_error"
        diag["error"] = str(exc)
    return diag


def _make_audit_entry(
    *,
    hook_event_id: str,
    mode: str,
    target_docs: Sequence[Path],
    outcome: str,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": _kst_timestamp(),
        "event_id": f"sha-{uuid4().hex[:12]}",
        "hook_event_id": hook_event_id,
        "mode": mode,
        "target_docs": [str(p) for p in target_docs],
        "outcome": outcome,
        "diagnostics": dict(diagnostics or {}),
    }


def _append_audit_entry(audit_log_path: Path, entry: Mapping[str, Any]) -> None:
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_audit_entries(audit_log_path: Path) -> list[dict[str, Any]]:
    if not audit_log_path.exists():
        return []
    parsed: list[dict[str, Any]] = []
    for line_no, raw in enumerate(audit_log_path.read_text(encoding="utf-8").splitlines(), start=1):
        row = raw.strip()
        if not row:
            continue
        try:
            value = json.loads(row)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid audit log JSON at line {line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"invalid audit log entry at line {line_no}: expected JSON object")
        parsed.append(value)
    return parsed


def _resolve_replay_entry(entries: Sequence[Mapping[str, Any]], selector: str) -> tuple[Mapping[str, Any], int]:
    if not entries:
        raise ValueError("status-hook audit trail is empty")

    token = selector.strip()
    if not token:
        raise ValueError("replay selector is required (entry id or 1-based index)")

    if token.isdigit():
        idx = int(token)
        if idx <= 0:
            raise ValueError("replay index must be >= 1")
        if idx > len(entries):
            raise ValueError(f"replay index out of range: {idx} (entries={len(entries)})")
        return entries[idx - 1], idx

    for i, entry in enumerate(entries, start=1):
        if str(entry.get("event_id") or "").strip() == token:
            return entry, i

    raise ValueError(f"replay entry id not found: {token}")


def replay_audit_entry(
    *,
    selector: str,
    docs_root: Path,
    audit_log_path: Path,
    apply: bool,
    timestamp: str | None,
) -> tuple[str, list[Path], Mapping[str, Any], int]:
    entries = _load_audit_entries(audit_log_path)
    selected, selected_index = _resolve_replay_entry(entries, selector)

    hook_event_id = str(selected.get("hook_event_id") or "").strip()
    if not hook_event_id:
        raise ValueError("selected audit entry is missing hook_event_id")

    previous_diag = selected.get("diagnostics") if isinstance(selected.get("diagnostics"), Mapping) else {}
    replay_timestamp = timestamp
    if replay_timestamp is None:
        selected_requested_timestamp = previous_diag.get("requested_timestamp") if isinstance(previous_diag, Mapping) else None
        if isinstance(selected_requested_timestamp, str) and selected_requested_timestamp.strip():
            replay_timestamp = selected_requested_timestamp

    if apply:
        changed = apply_event(hook_event_id, docs_root=docs_root, timestamp=replay_timestamp)
        outcome = "replayed_apply"
    else:
        originals, expected = _compute_expected_contents(
            hook_event_id,
            docs_root=docs_root,
            timestamp=replay_timestamp,
            preserve_existing_timestamp=False,
        )
        changed = [path for path, expected_content in expected.items() if expected_content != originals[path]]
        outcome = "replayed_dry_run"

    diagnostics = {
        "replay_selector": selector,
        "replay_source_event_id": selected.get("event_id"),
        "replay_source_index": selected_index,
        "replay_source_mode": selected.get("mode"),
        "requested_timestamp": replay_timestamp,
        "changed_docs": [str(p) for p in changed],
        "changed_docs_count": len(changed),
    }
    return hook_event_id, changed, diagnostics, selected_index


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", nargs="?", help="canonical event key (e.g. av4.kickoff.started)")
    parser.add_argument(
        "--docs-root",
        default=str(Path(__file__).resolve().parents[1] / "docs"),
        help="docs directory root (default: repo docs/)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="status timestamp override; default is current KST",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate/apply in-memory and print target files without writing",
    )
    parser.add_argument(
        "--drift-check",
        action="store_true",
        help="no-write mode: fail when docs diverge from canonical event output",
    )
    parser.add_argument(
        "--replay",
        default=None,
        help="replay prior audit entry by entry id or 1-based index",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --replay, actually apply changes (default is safe dry-run)",
    )
    parser.add_argument(
        "--audit-log",
        default=str(_default_audit_log_path()),
        help=f"append-only status-hook audit log path (default: {DEFAULT_AUDIT_LOG_RELATIVE})",
    )
    parser.add_argument(
        "--lock-file",
        default=str(_default_lockfile_path()),
        help=f"write lock path used for apply/replay --apply (default: {DEFAULT_LOCKFILE_RELATIVE})",
    )
    parser.add_argument(
        "--lock-stale-seconds",
        type=int,
        default=DEFAULT_LOCK_STALE_SECONDS,
        help=f"consider lock stale after N seconds (default: {DEFAULT_LOCK_STALE_SECONDS})",
    )
    parser.add_argument(
        "--force-lock",
        action="store_true",
        help="override stale write lock for apply/replay --apply",
    )
    parser.add_argument(
        "--validate-registry",
        action="store_true",
        help="validate status-hook event registry schema and exit",
    )
    parser.add_argument(
        "--detect-event",
        action="store_true",
        help="detect the canonical event that matches current docs and print it",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Explicit fail-fast validation in every runtime path.
    registry_errors = validate_event_registry(EVENT_REGISTRY)
    if registry_errors:
        print("[FAIL] Invalid status-hook event registry:")
        for error in registry_errors:
            print(f"  - {error}")
        return 1

    if args.validate_registry:
        print(f"[PASS] Status-hook event registry valid ({len(EVENT_REGISTRY)} event(s)).")
        return 0

    docs_root = Path(args.docs_root).resolve()
    if not docs_root.exists():
        parser.error(f"docs root not found: {docs_root}")

    audit_log_path = Path(args.audit_log).resolve()
    lockfile_path = Path(args.lock_file).resolve()

    if args.detect_event:
        if args.event:
            parser.error("event positional arg cannot be used with --detect-event")
        if args.replay:
            parser.error("--replay cannot be used with --detect-event")
        if args.apply:
            parser.error("--apply cannot be used with --detect-event")
        if args.drift_check:
            parser.error("--drift-check cannot be used with --detect-event")
        if args.dry_run:
            parser.error("--dry-run cannot be used with --detect-event")
        try:
            event_id = detect_matching_event(docs_root=docs_root)
        except ValueError as exc:
            print(f"[FAIL] {exc}")
            return 1
        print(event_id)
        return 0

    if args.replay:
        if args.event:
            parser.error("event positional arg cannot be used with --replay")

        mode = "replay-apply" if args.apply else "replay-dry-run"
        target_docs = [docs_root / p for p in EXPECTED_DOC_TRANSITIONS]

        lock_handle: WriteLockHandle | None = None
        lock_acquire_diag: Mapping[str, Any] | None = None
        entry: dict[str, Any] | None = None
        exit_code = 0

        try:
            if args.apply:
                lock_handle, lock_acquire_diag = _acquire_write_lock(
                    lockfile_path=lockfile_path,
                    stale_after_seconds=args.lock_stale_seconds,
                    force_override=args.force_lock,
                )
                print(f"[LOCK] Acquired status-hook write lock: {lockfile_path}")

            hook_event_id, changed, diagnostics, selected_index = replay_audit_entry(
                selector=args.replay,
                docs_root=docs_root,
                audit_log_path=audit_log_path,
                apply=args.apply,
                timestamp=args.timestamp,
            )

            if args.apply:
                diagnostics = dict(diagnostics)
                diagnostics["lock"] = {
                    "acquire": dict(lock_acquire_diag or {}),
                    "release": None,
                }

            entry = _make_audit_entry(
                hook_event_id=hook_event_id,
                mode=mode,
                target_docs=target_docs,
                outcome="success",
                diagnostics=diagnostics,
            )

            if args.apply:
                if changed:
                    print(f"[PASS] Replay apply updated docs from audit entry #{selected_index} ({args.replay}):")
                    for path in changed:
                        print(f"  - {path}")
                else:
                    print(f"[PASS] Replay apply found no file changes (entry #{selected_index}: {args.replay}).")
            else:
                if changed:
                    print(f"[DRY-RUN] Replay would update docs from audit entry #{selected_index} ({args.replay}):")
                    for path in changed:
                        print(f"  - {path}")
                else:
                    print(f"[DRY-RUN] Replay found no file changes (entry #{selected_index}: {args.replay}).")

        except StatusHookLockError as exc:
            print(f"[FAIL] Replay blocked by status-hook write lock: {exc}")
            entry = _make_audit_entry(
                hook_event_id=f"replay:{args.replay}",
                mode=mode,
                target_docs=target_docs,
                outcome="blocked_by_lock",
                diagnostics={"replay_selector": args.replay, "lock": dict(exc.diagnostics)},
            )
            exit_code = 1
        except ValueError as exc:
            print(f"[FAIL] Replay failed: {exc}")
            diagnostics: dict[str, Any] = {"replay_selector": args.replay, "error": str(exc)}
            if lock_acquire_diag is not None:
                diagnostics["lock"] = {"acquire": dict(lock_acquire_diag), "release": None}
            entry = _make_audit_entry(
                hook_event_id=f"replay:{args.replay}",
                mode=mode,
                target_docs=target_docs,
                outcome="error",
                diagnostics=diagnostics,
            )
            exit_code = 1
        finally:
            if lock_handle is not None:
                release_diag = _release_write_lock(lock_handle)
                print(f"[LOCK] Released status-hook write lock: {lockfile_path} ({release_diag.get('status')})")
                if entry is not None:
                    diagnostics = dict(entry.get("diagnostics") or {})
                    lock_diag = diagnostics.get("lock") if isinstance(diagnostics.get("lock"), Mapping) else {}
                    lock_diag = dict(lock_diag)
                    if lock_acquire_diag is not None and "acquire" not in lock_diag:
                        lock_diag["acquire"] = dict(lock_acquire_diag)
                    lock_diag["release"] = dict(release_diag)
                    diagnostics["lock"] = lock_diag
                    entry["diagnostics"] = diagnostics

        if entry is not None:
            _append_audit_entry(audit_log_path, entry)
        return exit_code

    if not args.event:
        parser.error("event is required unless --validate-registry or --replay is used")

    target_docs = [docs_root / p for p in EXPECTED_DOC_TRANSITIONS]

    if args.drift_check:
        try:
            drifted = drift_check_event(args.event, docs_root=docs_root, timestamp=args.timestamp)
        except ValueError as exc:
            print(f"[FAIL] Status hook drift-check failed: {exc}")
            _append_audit_entry(
                audit_log_path,
                _make_audit_entry(
                    hook_event_id=args.event,
                    mode="drift-check",
                    target_docs=target_docs,
                    outcome="error",
                    diagnostics={"error": str(exc), "requested_timestamp": args.timestamp},
                ),
            )
            return 1

        if drifted:
            print("[FAIL] Status hook drift detected for canonical event:")
            for path in drifted:
                print(f"  - {path}")
            print("[HINT] Run status_board_automation.py without --drift-check to reconcile docs.")
            _append_audit_entry(
                audit_log_path,
                _make_audit_entry(
                    hook_event_id=args.event,
                    mode="drift-check",
                    target_docs=target_docs,
                    outcome="drift_detected",
                    diagnostics={
                        "requested_timestamp": args.timestamp,
                        "drifted_docs": [str(p) for p in drifted],
                        "drifted_docs_count": len(drifted),
                    },
                ),
            )
            return 1

        print("[PASS] Status hook drift check passed (docs already match canonical event output).")
        _append_audit_entry(
            audit_log_path,
            _make_audit_entry(
                hook_event_id=args.event,
                mode="drift-check",
                target_docs=target_docs,
                outcome="pass",
                diagnostics={"requested_timestamp": args.timestamp, "drifted_docs": [], "drifted_docs_count": 0},
            ),
        )
        return 0

    if args.dry_run:
        originals, expected = _compute_expected_contents(
            args.event,
            docs_root=docs_root,
            timestamp=args.timestamp,
            preserve_existing_timestamp=False,
        )
        changed = [path for path, expected_content in expected.items() if expected_content != originals[path]]
        if changed:
            print("[DRY-RUN] Would update:")
            for path in changed:
                print(f"  - {path}")
        else:
            print("[DRY-RUN] No file changes required (already up to date).")
        return 0

    lock_handle: WriteLockHandle | None = None
    lock_acquire_diag: Mapping[str, Any] | None = None
    entry: dict[str, Any] | None = None

    try:
        lock_handle, lock_acquire_diag = _acquire_write_lock(
            lockfile_path=lockfile_path,
            stale_after_seconds=args.lock_stale_seconds,
            force_override=args.force_lock,
        )
        print(f"[LOCK] Acquired status-hook write lock: {lockfile_path}")

        changed = apply_event(args.event, docs_root=docs_root, timestamp=args.timestamp)

        if changed:
            print("[PASS] Updated status docs from canonical event:")
            for path in changed:
                print(f"  - {path}")
        else:
            print("[PASS] Status docs already matched canonical event (no changes).")

        entry = _make_audit_entry(
            hook_event_id=args.event,
            mode="apply",
            target_docs=target_docs,
            outcome="updated" if changed else "noop",
            diagnostics={
                "requested_timestamp": args.timestamp,
                "changed_docs": [str(p) for p in changed],
                "changed_docs_count": len(changed),
                "lock": {"acquire": dict(lock_acquire_diag or {}), "release": None},
            },
        )

    except StatusHookLockError as exc:
        print(f"[FAIL] Status hook apply blocked by write lock: {exc}")
        _append_audit_entry(
            audit_log_path,
            _make_audit_entry(
                hook_event_id=args.event,
                mode="apply",
                target_docs=target_docs,
                outcome="blocked_by_lock",
                diagnostics={"requested_timestamp": args.timestamp, "lock": dict(exc.diagnostics)},
            ),
        )
        return 1
    except ValueError as exc:
        print(f"[FAIL] Status hook apply failed: {exc}")
        _append_audit_entry(
            audit_log_path,
            _make_audit_entry(
                hook_event_id=args.event,
                mode="apply",
                target_docs=target_docs,
                outcome="error",
                diagnostics={
                    "error": str(exc),
                    "requested_timestamp": args.timestamp,
                    "lock": {"acquire": dict(lock_acquire_diag or {}), "release": None} if lock_acquire_diag else None,
                },
            ),
        )
        return 1
    finally:
        if lock_handle is not None:
            release_diag = _release_write_lock(lock_handle)
            print(f"[LOCK] Released status-hook write lock: {lockfile_path} ({release_diag.get('status')})")
            if entry is not None:
                diagnostics = dict(entry.get("diagnostics") or {})
                lock_diag = diagnostics.get("lock") if isinstance(diagnostics.get("lock"), Mapping) else {}
                lock_diag = dict(lock_diag)
                lock_diag["release"] = dict(release_diag)
                diagnostics["lock"] = lock_diag
                entry["diagnostics"] = diagnostics

    if entry is None:
        return 1

    _append_audit_entry(audit_log_path, entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
