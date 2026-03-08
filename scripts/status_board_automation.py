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
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
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


@dataclass(frozen=True)
class EventRegistryEntry:
    event_id: str
    description: str
    expected_doc_transitions: tuple[str, ...]
    spec: CanonicalEventSpec


STATUS_TIMESTAMP_PREFIX = "Status timestamp: "
EXPECTED_DOC_TRANSITIONS = (
    "STATUS_BOARD_CURRENT.md",
    "PLAN_NEXT_WEEK.md",
    "BACKLOG_NEXT_WEEK.md",
)


EVENT_REGISTRY: tuple[EventRegistryEntry, ...] = (
    EventRegistryEntry(
        event_id="av4.kickoff.started",
        description="Initialize AV4 kickoff docs baseline across status/plan/backlog.",
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


@dataclass(frozen=True)
class DocTransformation:
    rel_path: str
    render: Callable[[str, CanonicalEventSpec], str]


def _kst_timestamp() -> str:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.strftime("%Y-%m-%d %H:%M KST (Asia/Seoul)")


def _replace_once(content: str, old: str, new: str, *, file_path: Path) -> str:
    if old not in content:
        raise ValueError(f"expected snippet not found in {file_path}: {old}")
    return content.replace(old, new, 1)


def _extract_status_timestamp(content: str) -> str | None:
    for line in content.splitlines():
        if line.startswith(STATUS_TIMESTAMP_PREFIX):
            return line.removeprefix(STATUS_TIMESTAMP_PREFIX)
    return None


def _render_status_board(content: str, spec: CanonicalEventSpec, *, file_path: Path, timestamp: str) -> str:
    updated = content
    updated = _replace_once(
        updated,
        "- **Mode:** AV4 Kickoff",
        f"- **Mode:** {spec.mode}",
        file_path=file_path,
    )
    updated = _replace_once(
        updated,
        "- **Scope:** AV4 wave planning + kickoff execution start",
        f"- **Scope:** {spec.scope}",
        file_path=file_path,
    )
    updated = _replace_once(
        updated,
        "- **State:** AV3 closed on `main`; AV4 kickoff package started",
        f"- **State:** {spec.state}",
        file_path=file_path,
    )
    updated = _replace_once(
        updated,
        "- **AV4:** 🚧 Kickoff started (plan + backlog published)",
        f"- **AV4:** {spec.av4_snapshot}",
        file_path=file_path,
    )

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
    updated = _replace_once(
        content,
        "# PLAN — Next Wave (AV4 Kickoff Active)",
        spec.plan_title,
        file_path=file_path,
    )
    updated = _replace_once(
        updated,
        "- AV4 kickoff package is now active (`docs/AUTONOMOUS_V4_WAVE_PLAN.md`, `docs/AUTONOMOUS_V4_BACKLOG.md`).",
        spec.plan_av4_snapshot,
        file_path=file_path,
    )
    return updated


def _render_backlog(content: str, spec: CanonicalEventSpec, *, file_path: Path) -> str:
    updated = _replace_once(
        content,
        "# BACKLOG — Next Wave (AV4 Kickoff Queue)",
        spec.backlog_title,
        file_path=file_path,
    )
    updated = _replace_once(
        updated,
        "- AV4 kickoff: 🚧 started",
        spec.backlog_av4_snapshot,
        file_path=file_path,
    )
    return updated


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

    originals = {
        status_path: status_path.read_text(encoding="utf-8"),
        plan_path: plan_path.read_text(encoding="utf-8"),
        backlog_path: backlog_path.read_text(encoding="utf-8"),
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

    originals = {
        status_path: status_path.read_text(encoding="utf-8"),
        plan_path: plan_path.read_text(encoding="utf-8"),
        backlog_path: backlog_path.read_text(encoding="utf-8"),
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

    return drifted


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
        "--validate-registry",
        action="store_true",
        help="validate status-hook event registry schema and exit",
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

    if not args.event:
        parser.error("event is required unless --validate-registry is used")

    docs_root = Path(args.docs_root).resolve()
    if not docs_root.exists():
        parser.error(f"docs root not found: {docs_root}")

    if args.drift_check:
        drifted = drift_check_event(args.event, docs_root=docs_root, timestamp=args.timestamp)
        if drifted:
            print("[FAIL] Status hook drift detected for canonical event:")
            for path in drifted:
                print(f"  - {path}")
            print("[HINT] Run status_board_automation.py without --drift-check to reconcile docs.")
            return 1
        print("[PASS] Status hook drift check passed (docs already match canonical event output).")
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

    changed = apply_event(args.event, docs_root=docs_root, timestamp=args.timestamp)
    if changed:
        print("[PASS] Updated status docs from canonical event:")
        for path in changed:
            print(f"  - {path}")
    else:
        print("[PASS] Status docs already matched canonical event (no changes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
