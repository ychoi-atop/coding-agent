"""Parallel fixer agents — concurrent repair of independent failure categories.

When validation failures span multiple independent categories affecting disjoint
file sets (e.g. lint errors in ``utils.py`` and test failures in ``test_api.py``),
this module enables running separate fixer LLM calls in parallel, then merging
the resulting changesets.

Controlled via ``quality_profile["parallel_fixer"]``.  When disabled (default),
the existing sequential behaviour is preserved exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple

from .failure_analyzer import (
    FailureAnalysis,
    FailureCategory,
    REPAIR_STRATEGIES,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FailureGroup:
    """A group of failures sharing the same category."""

    category: FailureCategory
    analyses: List[FailureAnalysis]
    files: Set[str]  # union of all failing_files
    validator_rows: List[Dict[str, Any]]  # filtered validation rows

    @property
    def priority(self) -> int:
        """Category priority from REPAIR_STRATEGIES (lower = fix first)."""
        return REPAIR_STRATEGIES.get(self.category, ("", 99))[1]


@dataclass
class MergeConflict:
    """A conflict detected during changeset merge."""

    path: str
    categories: List[FailureCategory]
    kept_category: FailureCategory


@dataclass
class ChangesetMergeResult:
    """Result of merging multiple changesets."""

    merged_changeset: Dict[str, Any]
    conflicts: List[MergeConflict]
    stats: Dict[str, int]


@dataclass(frozen=True)
class ParallelFixerConfig:
    """Configuration for parallel fixer execution."""

    enabled: bool = False
    min_groups: int = 2  # minimum disjoint groups to trigger parallel
    max_parallel: int = 3  # max concurrent LLM calls


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def resolve_parallel_fixer_config(
    quality_profile: Dict[str, Any] | None,
) -> ParallelFixerConfig:
    """Extract :class:`ParallelFixerConfig` from *quality_profile*.

    Returns a disabled config when ``quality_profile`` is ``None`` or the
    ``parallel_fixer`` key is missing/invalid.
    """
    if not isinstance(quality_profile, dict):
        return ParallelFixerConfig()

    raw = quality_profile.get("parallel_fixer")
    if not isinstance(raw, dict):
        return ParallelFixerConfig()

    enabled = raw.get("enabled", False) is True
    if not enabled:
        return ParallelFixerConfig()

    min_groups = _safe_int(raw.get("min_groups", 2))
    if min_groups < 2:
        min_groups = 2

    max_parallel = _safe_int(raw.get("max_parallel", 3))
    if max_parallel < 1:
        max_parallel = 3

    return ParallelFixerConfig(
        enabled=True,
        min_groups=min_groups,
        max_parallel=max_parallel,
    )


# ---------------------------------------------------------------------------
# Failure partitioning
# ---------------------------------------------------------------------------


def partition_failures(
    analyses: List[FailureAnalysis],
    validation_rows: List[Dict[str, Any]],
) -> List[FailureGroup]:
    """Group failures by category, collecting affected files.

    Each group contains the analyses for one category, the union of all
    ``failing_files``, and the subset of *validation_rows* that belong to
    the validators in that category.

    Groups are returned sorted by category priority (lower = higher priority).
    """
    if not analyses:
        return []

    # Group analyses by category
    by_category: Dict[FailureCategory, List[FailureAnalysis]] = {}
    for analysis in analyses:
        by_category.setdefault(analysis.category, []).append(analysis)

    # Build validator_name → category mapping for row filtering
    validator_to_category: Dict[str, FailureCategory] = {}
    for analysis in analyses:
        # First-seen category wins (consistent with priority ordering)
        if analysis.validator_name not in validator_to_category:
            validator_to_category[analysis.validator_name] = analysis.category

    groups: List[FailureGroup] = []
    for category, cat_analyses in by_category.items():
        # Union all failing_files
        all_files: Set[str] = set()
        for a in cat_analyses:
            all_files.update(a.failing_files)

        # Filter validation rows: failed rows whose validator belongs to this category
        cat_validator_names = {a.validator_name for a in cat_analyses}
        filtered_rows = [
            row
            for row in validation_rows
            if not row.get("ok", True) and row.get("name") in cat_validator_names
        ]

        groups.append(
            FailureGroup(
                category=category,
                analyses=cat_analyses,
                files=all_files,
                validator_rows=filtered_rows,
            )
        )

    # Sort by priority (lower number = fix first)
    groups.sort(key=lambda g: g.priority)
    return groups


# ---------------------------------------------------------------------------
# Disjoint group detection
# ---------------------------------------------------------------------------


def find_disjoint_groups(groups: List[FailureGroup]) -> List[FailureGroup]:
    """Select groups with non-overlapping file sets.

    Uses a greedy algorithm: process groups by priority order. For each group,
    if its files don't overlap with any already-selected group, include it.
    Groups with empty file sets are always considered disjoint (no file conflict
    possible).

    Returns the subset of groups that can safely run in parallel.
    """
    if len(groups) <= 1:
        return list(groups)

    selected: List[FailureGroup] = []
    seen_files: Set[str] = set()

    for group in groups:
        # Empty files → no conflict possible, always include
        if not group.files or not (group.files & seen_files):
            selected.append(group)
            seen_files.update(group.files)

    return selected


# ---------------------------------------------------------------------------
# Changeset merging
# ---------------------------------------------------------------------------


def _empty_changeset() -> Dict[str, Any]:
    """Return an empty changeset conforming to CHANGESET_SCHEMA."""
    return {
        "role": "fixer",
        "summary": "",
        "changes": [],
        "notes": [],
        "handoff": {
            "Summary": "",
            "Changed Files": [],
        },
    }


def merge_changesets(
    changesets: List[Tuple[FailureCategory, Dict[str, Any]]],
) -> ChangesetMergeResult:
    """Merge multiple changesets from parallel fixers into a single changeset.

    **Conflict resolution**: when multiple changesets modify the same file,
    the changeset from the higher-priority category (lower priority number)
    wins.  All conflicts are recorded in :class:`MergeConflict` entries.

    Metadata (``summary``, ``notes``, ``handoff``) is combined from all
    changesets.
    """
    if not changesets:
        return ChangesetMergeResult(
            merged_changeset=_empty_changeset(),
            conflicts=[],
            stats={"merge_count": 0, "conflict_count": 0, "total_changes": 0},
        )

    if len(changesets) == 1:
        return ChangesetMergeResult(
            merged_changeset=changesets[0][1],
            conflicts=[],
            stats={"merge_count": 1, "conflict_count": 0, "total_changes": len(changesets[0][1].get("changes", []))},
        )

    # Group changes by path
    by_path: Dict[str, List[Tuple[FailureCategory, Dict[str, Any]]]] = {}
    for category, changeset in changesets:
        for change in changeset.get("changes", []):
            path = change.get("path", "")
            by_path.setdefault(path, []).append((category, change))

    # Resolve per-path
    merged_changes: List[Dict[str, Any]] = []
    conflicts: List[MergeConflict] = []

    for path, path_changes in sorted(by_path.items()):
        if len(path_changes) == 1:
            # No conflict — single source
            merged_changes.append(path_changes[0][1])
        else:
            # Conflict — priority resolution
            categories = [cat for cat, _ in path_changes]
            sorted_changes = sorted(
                path_changes,
                key=lambda x: REPAIR_STRATEGIES.get(x[0], ("", 99))[1],
            )
            kept_category, kept_change = sorted_changes[0]
            merged_changes.append(kept_change)
            conflicts.append(
                MergeConflict(
                    path=path,
                    categories=categories,
                    kept_category=kept_category,
                )
            )

    # Merge metadata
    summaries = []
    all_notes: List[str] = []
    for category, changeset in changesets:
        s = changeset.get("summary", "")
        if s:
            summaries.append(f"[{category.value}] {s}")
        all_notes.extend(changeset.get("notes", []))

    changed_files = list({ch.get("path", "") for ch in merged_changes})

    # Use first changeset's handoff as base
    base_handoff = changesets[0][1].get("handoff", {})
    if isinstance(base_handoff, dict):
        merged_handoff = dict(base_handoff)
    else:
        merged_handoff = {}
    merged_handoff["Summary"] = f"Parallel fix: {len(changesets)} category groups"
    merged_handoff["Changed Files"] = changed_files

    merged_changeset = {
        "role": "parallel_fixer",
        "summary": "; ".join(summaries) if summaries else "Parallel fix",
        "changes": merged_changes,
        "notes": all_notes,
        "handoff": merged_handoff,
    }

    return ChangesetMergeResult(
        merged_changeset=merged_changeset,
        conflicts=conflicts,
        stats={
            "merge_count": len(changesets),
            "conflict_count": len(conflicts),
            "total_changes": len(merged_changes),
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object) -> int:
    """Coerce to int, returning 0 on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
