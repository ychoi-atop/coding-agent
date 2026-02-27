"""Tests for autodev.parallel_fixer module."""

from __future__ import annotations

from autodev.failure_analyzer import FailureAnalysis, FailureCategory
from autodev.parallel_fixer import (
    FailureGroup,
    find_disjoint_groups,
    merge_changesets,
    partition_failures,
    resolve_parallel_fixer_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analysis(
    validator_name: str = "ruff",
    category: FailureCategory = FailureCategory.LINT_ERROR,
    failing_files: list[str] | None = None,
) -> FailureAnalysis:
    return FailureAnalysis(
        validator_name=validator_name,
        category=category,
        raw_error_classification=None,
        summary=f"{category.value} in {validator_name}",
        failing_files=failing_files or [],
        failing_lines=[],
        confidence=0.9,
    )


def _make_validation_row(
    name: str = "ruff",
    ok: bool = False,
) -> dict:
    return {"name": name, "ok": ok, "status": "fail" if not ok else "pass"}


def _make_changeset(
    changes: list[dict] | None = None,
    summary: str = "fix",
    notes: list[str] | None = None,
) -> dict:
    return {
        "role": "fixer",
        "summary": summary,
        "changes": changes or [],
        "notes": notes or [],
        "handoff": {"Summary": summary, "Changed Files": []},
    }


def _make_group(
    category: FailureCategory,
    files: set[str] | None = None,
) -> FailureGroup:
    return FailureGroup(
        category=category,
        analyses=[_make_analysis(category=category, failing_files=list(files or set()))],
        files=files or set(),
        validator_rows=[],
    )


# ---------------------------------------------------------------------------
# Test 1-3: resolve_parallel_fixer_config
# ---------------------------------------------------------------------------


def test_resolve_config_disabled_default():
    """None or {} quality_profile → disabled config."""
    cfg1 = resolve_parallel_fixer_config(None)
    assert cfg1.enabled is False

    cfg2 = resolve_parallel_fixer_config({})
    assert cfg2.enabled is False

    cfg3 = resolve_parallel_fixer_config({"parallel_fixer": {}})
    assert cfg3.enabled is False


def test_resolve_config_enabled():
    """Valid config → correctly parsed."""
    profile = {
        "parallel_fixer": {
            "enabled": True,
            "min_groups": 3,
            "max_parallel": 5,
        }
    }
    cfg = resolve_parallel_fixer_config(profile)
    assert cfg.enabled is True
    assert cfg.min_groups == 3
    assert cfg.max_parallel == 5


def test_resolve_config_invalid_values():
    """Invalid values → safe defaults."""
    profile = {
        "parallel_fixer": {
            "enabled": True,
            "min_groups": 0,  # too low → 2
            "max_parallel": -1,  # too low → 3
        }
    }
    cfg = resolve_parallel_fixer_config(profile)
    assert cfg.enabled is True
    assert cfg.min_groups == 2
    assert cfg.max_parallel == 3


# ---------------------------------------------------------------------------
# Test 4-7: partition_failures
# ---------------------------------------------------------------------------


def test_partition_single_category():
    """All failures same category → 1 group."""
    analyses = [
        _make_analysis("ruff", FailureCategory.LINT_ERROR, ["a.py"]),
        _make_analysis("ruff", FailureCategory.LINT_ERROR, ["b.py"]),
    ]
    rows = [
        _make_validation_row("ruff", ok=False),
    ]
    groups = partition_failures(analyses, rows)
    assert len(groups) == 1
    assert groups[0].category == FailureCategory.LINT_ERROR
    assert groups[0].files == {"a.py", "b.py"}
    assert len(groups[0].analyses) == 2


def test_partition_multiple_categories():
    """Mixed categories → multiple groups sorted by priority."""
    analyses = [
        _make_analysis("ruff", FailureCategory.LINT_ERROR, ["a.py"]),
        _make_analysis("mypy", FailureCategory.TYPE_ERROR, ["b.py"]),
        _make_analysis("pytest", FailureCategory.TEST_LOGIC_ERROR, ["c.py"]),
    ]
    rows = [
        _make_validation_row("ruff", ok=False),
        _make_validation_row("mypy", ok=False),
        _make_validation_row("pytest", ok=False),
    ]
    groups = partition_failures(analyses, rows)
    assert len(groups) == 3
    # Sorted by priority: TYPE_ERROR(3) < LINT_ERROR(4) < TEST_LOGIC_ERROR(6)
    assert groups[0].category == FailureCategory.TYPE_ERROR
    assert groups[1].category == FailureCategory.LINT_ERROR
    assert groups[2].category == FailureCategory.TEST_LOGIC_ERROR


def test_partition_empty():
    """Empty analyses → empty groups."""
    assert partition_failures([], []) == []


def test_partition_filters_validator_rows():
    """Only failed rows matching category's validators are included."""
    analyses = [
        _make_analysis("ruff", FailureCategory.LINT_ERROR, ["a.py"]),
        _make_analysis("mypy", FailureCategory.TYPE_ERROR, ["b.py"]),
    ]
    rows = [
        _make_validation_row("ruff", ok=False),
        _make_validation_row("mypy", ok=False),
        _make_validation_row("pytest", ok=True),  # ok=True, should be excluded
    ]
    groups = partition_failures(analyses, rows)
    assert len(groups) == 2

    # TYPE_ERROR group (priority 3) comes first
    type_group = groups[0]
    assert type_group.category == FailureCategory.TYPE_ERROR
    assert len(type_group.validator_rows) == 1
    assert type_group.validator_rows[0]["name"] == "mypy"

    lint_group = groups[1]
    assert lint_group.category == FailureCategory.LINT_ERROR
    assert len(lint_group.validator_rows) == 1
    assert lint_group.validator_rows[0]["name"] == "ruff"


# ---------------------------------------------------------------------------
# Test 8-11: find_disjoint_groups
# ---------------------------------------------------------------------------


def test_disjoint_all_independent():
    """No file overlap → all selected."""
    groups = [
        _make_group(FailureCategory.LINT_ERROR, {"a.py"}),
        _make_group(FailureCategory.TYPE_ERROR, {"b.py"}),
        _make_group(FailureCategory.TEST_LOGIC_ERROR, {"c.py"}),
    ]
    result = find_disjoint_groups(groups)
    assert len(result) == 3


def test_disjoint_some_overlap():
    """Partial overlap → overlapping group excluded."""
    groups = [
        _make_group(FailureCategory.TYPE_ERROR, {"a.py", "shared.py"}),
        _make_group(FailureCategory.LINT_ERROR, {"b.py"}),
        _make_group(FailureCategory.TEST_LOGIC_ERROR, {"shared.py", "c.py"}),
    ]
    result = find_disjoint_groups(groups)
    # TYPE_ERROR selected first (priority 3), LINT_ERROR second (priority 4)
    # TEST_LOGIC_ERROR skipped (shares shared.py with TYPE_ERROR)
    assert len(result) == 2
    assert result[0].category == FailureCategory.TYPE_ERROR
    assert result[1].category == FailureCategory.LINT_ERROR


def test_disjoint_all_overlap():
    """All groups share files → only highest priority selected."""
    groups = [
        _make_group(FailureCategory.LINT_ERROR, {"shared.py"}),
        _make_group(FailureCategory.TYPE_ERROR, {"shared.py"}),
        _make_group(FailureCategory.TEST_LOGIC_ERROR, {"shared.py"}),
    ]
    result = find_disjoint_groups(groups)
    # Only first in priority order is selected
    assert len(result) == 1


def test_disjoint_empty_files():
    """Groups with empty file sets → treated as independent."""
    groups = [
        _make_group(FailureCategory.LINT_ERROR, set()),
        _make_group(FailureCategory.TYPE_ERROR, set()),
        _make_group(FailureCategory.TEST_LOGIC_ERROR, {"a.py"}),
    ]
    result = find_disjoint_groups(groups)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Test 12-17: merge_changesets
# ---------------------------------------------------------------------------


def test_merge_no_conflicts():
    """Disjoint changes → simple concatenation."""
    changesets = [
        (
            FailureCategory.LINT_ERROR,
            _make_changeset(
                changes=[{"op": "write", "path": "a.py", "content": "fixed a"}],
                summary="lint fix",
            ),
        ),
        (
            FailureCategory.TYPE_ERROR,
            _make_changeset(
                changes=[{"op": "write", "path": "b.py", "content": "fixed b"}],
                summary="type fix",
            ),
        ),
    ]
    result = merge_changesets(changesets)
    assert result.stats["merge_count"] == 2
    assert result.stats["conflict_count"] == 0
    assert result.stats["total_changes"] == 2
    assert len(result.conflicts) == 0
    # Both changes present
    paths = {c["path"] for c in result.merged_changeset["changes"]}
    assert paths == {"a.py", "b.py"}


def test_merge_conflict_priority():
    """Same file modified → higher priority category wins."""
    changesets = [
        (
            FailureCategory.TEST_LOGIC_ERROR,  # priority 6
            _make_changeset(
                changes=[{"op": "write", "path": "shared.py", "content": "test fix"}],
                summary="test fix",
            ),
        ),
        (
            FailureCategory.SYNTAX_ERROR,  # priority 0 (highest)
            _make_changeset(
                changes=[{"op": "write", "path": "shared.py", "content": "syntax fix"}],
                summary="syntax fix",
            ),
        ),
    ]
    result = merge_changesets(changesets)
    assert result.stats["conflict_count"] == 1
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.path == "shared.py"
    assert conflict.kept_category == FailureCategory.SYNTAX_ERROR
    # Content from syntax fix (higher priority) should win
    assert result.merged_changeset["changes"][0]["content"] == "syntax fix"


def test_merge_single_changeset():
    """Single changeset → returned as-is."""
    cs = _make_changeset(
        changes=[{"op": "write", "path": "a.py", "content": "fix"}],
        summary="single fix",
    )
    result = merge_changesets([(FailureCategory.LINT_ERROR, cs)])
    assert result.stats["merge_count"] == 1
    assert result.stats["conflict_count"] == 0
    assert result.merged_changeset is cs  # same object


def test_merge_empty():
    """Empty input → empty changeset."""
    result = merge_changesets([])
    assert result.stats["merge_count"] == 0
    assert result.merged_changeset["changes"] == []


def test_merge_metadata_combined():
    """Summaries, notes, handoff combined from all changesets."""
    changesets = [
        (
            FailureCategory.LINT_ERROR,
            _make_changeset(
                changes=[{"op": "write", "path": "a.py", "content": "a"}],
                summary="fix lint",
                notes=["note1"],
            ),
        ),
        (
            FailureCategory.TYPE_ERROR,
            _make_changeset(
                changes=[{"op": "write", "path": "b.py", "content": "b"}],
                summary="fix types",
                notes=["note2", "note3"],
            ),
        ),
    ]
    result = merge_changesets(changesets)
    assert "[lint_error]" in result.merged_changeset["summary"]
    assert "[type_error]" in result.merged_changeset["summary"]
    assert len(result.merged_changeset["notes"]) == 3
    assert "Parallel fix" in result.merged_changeset["handoff"]["Summary"]


def test_merge_conflict_records_all():
    """Conflict record includes all competing categories."""
    changesets = [
        (
            FailureCategory.LINT_ERROR,  # priority 4
            _make_changeset(
                changes=[{"op": "write", "path": "x.py", "content": "lint"}],
            ),
        ),
        (
            FailureCategory.TYPE_ERROR,  # priority 3 (wins)
            _make_changeset(
                changes=[{"op": "write", "path": "x.py", "content": "type"}],
            ),
        ),
        (
            FailureCategory.SECURITY_FINDING,  # priority 7
            _make_changeset(
                changes=[{"op": "write", "path": "x.py", "content": "sec"}],
            ),
        ),
    ]
    result = merge_changesets(changesets)
    assert result.stats["conflict_count"] == 1
    conflict = result.conflicts[0]
    assert conflict.kept_category == FailureCategory.TYPE_ERROR
    assert len(conflict.categories) == 3
    # Verify all three categories are recorded
    cat_values = {c.value for c in conflict.categories}
    assert cat_values == {"lint_error", "type_error", "security_finding"}


# ---------------------------------------------------------------------------
# Test: FailureGroup.priority
# ---------------------------------------------------------------------------


def test_failure_group_priority():
    """Priority comes from REPAIR_STRATEGIES."""
    g1 = _make_group(FailureCategory.SYNTAX_ERROR)
    g2 = _make_group(FailureCategory.LINT_ERROR)
    g3 = _make_group(FailureCategory.UNKNOWN)
    assert g1.priority == 0
    assert g2.priority == 4
    assert g3.priority == 9


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


def test_partition_multiple_validators_same_category():
    """Multiple validators in same category → merged into one group."""
    analyses = [
        _make_analysis("ruff", FailureCategory.LINT_ERROR, ["a.py"]),
        _make_analysis("bandit", FailureCategory.SECURITY_FINDING, ["b.py"]),
        _make_analysis("semgrep", FailureCategory.SECURITY_FINDING, ["c.py"]),
    ]
    rows = [
        _make_validation_row("ruff", ok=False),
        _make_validation_row("bandit", ok=False),
        _make_validation_row("semgrep", ok=False),
    ]
    groups = partition_failures(analyses, rows)
    assert len(groups) == 2
    # Find security group
    sec_group = [g for g in groups if g.category == FailureCategory.SECURITY_FINDING][0]
    assert sec_group.files == {"b.py", "c.py"}
    assert len(sec_group.analyses) == 2
    assert len(sec_group.validator_rows) == 2


def test_find_disjoint_single_group():
    """Single group → returned as-is."""
    groups = [_make_group(FailureCategory.LINT_ERROR, {"a.py"})]
    result = find_disjoint_groups(groups)
    assert len(result) == 1
    assert result[0] is groups[0]


def test_find_disjoint_empty():
    """Empty input → empty result."""
    assert find_disjoint_groups([]) == []
