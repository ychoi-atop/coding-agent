"""Tests for autodev.smart_scope module."""

from __future__ import annotations

from autodev.smart_scope import (
    SmartScopeConfig,
    apply_smart_scope,
    expand_with_test_mapping,
    extract_changed_files,
    resolve_smart_scope_config,
)
from autodev.workspace import Change


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    enabled: bool = True,
    mode: str = "narrow",
    always_run: frozenset[str] | None = None,
    test_source_mapping: bool = True,
) -> SmartScopeConfig:
    return SmartScopeConfig(
        enabled=enabled,
        mode=mode,
        always_run=always_run or frozenset(),
        test_source_mapping=test_source_mapping,
    )


def _make_change(op: str = "write", path: str = "src/main.py") -> Change:
    return Change(op=op, path=path, content="# content")


# ---------------------------------------------------------------------------
# Test 1-3: resolve_smart_scope_config
# ---------------------------------------------------------------------------


def test_resolve_config_disabled_default():
    """None/{}/missing key → disabled config."""
    assert resolve_smart_scope_config(None).enabled is False
    assert resolve_smart_scope_config({}).enabled is False
    assert resolve_smart_scope_config({"smart_scope": "bad"}).enabled is False
    assert resolve_smart_scope_config({"smart_scope": {"enabled": False}}).enabled is False


def test_resolve_config_enabled():
    """All fields parsed correctly."""
    cfg = resolve_smart_scope_config({
        "smart_scope": {
            "enabled": True,
            "mode": "conservative",
            "always_run": ["ruff", "pytest"],
            "test_source_mapping": False,
        },
    })
    assert cfg.enabled is True
    assert cfg.mode == "conservative"
    assert cfg.always_run == frozenset({"ruff", "pytest"})
    assert cfg.test_source_mapping is False


def test_resolve_config_invalid_mode():
    """Invalid mode → 'narrow' fallback."""
    cfg = resolve_smart_scope_config({
        "smart_scope": {
            "enabled": True,
            "mode": "turbo",
        },
    })
    assert cfg.mode == "narrow"


# ---------------------------------------------------------------------------
# Test 4-6: extract_changed_files
# ---------------------------------------------------------------------------


def test_extract_changed_files_basic():
    """write/patch/delete ops → unique paths."""
    changes = [
        _make_change("write", "src/a.py"),
        _make_change("patch", "src/b.py"),
        _make_change("delete", "old/c.py"),
    ]
    result = extract_changed_files(changes)
    assert result == ["src/a.py", "src/b.py", "old/c.py"]


def test_extract_changed_files_dedup():
    """Duplicate paths deduplicated, preserving order."""
    changes = [
        _make_change("write", "src/a.py"),
        _make_change("patch", "src/a.py"),
        _make_change("write", "src/b.py"),
    ]
    result = extract_changed_files(changes)
    assert result == ["src/a.py", "src/b.py"]


def test_extract_changed_files_empty():
    """Empty list → empty result."""
    assert extract_changed_files([]) == []


# ---------------------------------------------------------------------------
# Test 7-10: expand_with_test_mapping
# ---------------------------------------------------------------------------


def test_expand_source_to_test():
    """src/foo.py → tests/test_foo.py added."""
    files = ["src/foo.py"]
    expanded = expand_with_test_mapping(files)
    assert "src/foo.py" in expanded
    assert "tests/test_foo.py" in expanded
    assert len(expanded) == 2


def test_expand_test_to_source():
    """tests/test_foo.py → foo.py added (basename stripped)."""
    files = ["tests/test_foo.py"]
    expanded = expand_with_test_mapping(files)
    assert "tests/test_foo.py" in expanded
    assert "foo.py" in expanded
    assert len(expanded) == 2


def test_expand_non_python():
    """.yml file → no mapping."""
    files = ["docker-compose.yml"]
    expanded = expand_with_test_mapping(files)
    assert expanded == ["docker-compose.yml"]


def test_expand_no_duplicates():
    """Already-present counterpart not duplicated."""
    files = ["src/foo.py", "tests/test_foo.py"]
    expanded = expand_with_test_mapping(files)
    # test_foo.py's counterpart "foo.py" added, but tests/test_foo.py already exists
    assert len(set(expanded)) == len(expanded)  # no duplicates


# ---------------------------------------------------------------------------
# Test 11-12: apply_smart_scope passthrough
# ---------------------------------------------------------------------------


def test_scope_disabled_passthrough():
    """Disabled config → original run_set unchanged."""
    cfg = _make_config(enabled=False)
    run_set = ["ruff", "pytest", "docker_build"]
    changes = [_make_change("write", "src/main.py")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert scoped == run_set
    assert result.removed_validators == []


def test_scope_no_changes_passthrough():
    """Empty changes → original run_set unchanged."""
    cfg = _make_config(enabled=True)
    run_set = ["ruff", "pytest"]

    scoped, result = apply_smart_scope(run_set, [], cfg)
    assert scoped == run_set
    assert result.changed_files == []


# ---------------------------------------------------------------------------
# Test 13: narrow mode filters irrelevant validators
# ---------------------------------------------------------------------------


def test_scope_narrows_python_only():
    """Python-only changes → docker_build removed."""
    cfg = _make_config(enabled=True, mode="narrow")
    run_set = ["ruff", "pytest", "docker_build"]
    changes = [_make_change("write", "src/main.py")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert "ruff" in scoped
    assert "pytest" in scoped
    assert "docker_build" not in scoped
    assert "docker_build" in result.removed_validators


# ---------------------------------------------------------------------------
# Test 14: always_run kept regardless
# ---------------------------------------------------------------------------


def test_scope_keeps_always_run():
    """always_run validators kept even when irrelevant."""
    cfg = _make_config(
        enabled=True,
        mode="narrow",
        always_run=frozenset({"docker_build"}),
    )
    run_set = ["ruff", "docker_build"]
    changes = [_make_change("write", "src/main.py")]

    scoped, _ = apply_smart_scope(run_set, changes, cfg)
    assert "docker_build" in scoped
    assert "ruff" in scoped


# ---------------------------------------------------------------------------
# Test 15: safety — all removed → fallback to original
# ---------------------------------------------------------------------------


def test_scope_all_removed_safety():
    """When all validators would be removed, keep original."""
    cfg = _make_config(enabled=True, mode="narrow")
    # Only pip_audit in run_set, but changes are to .js files → irrelevant
    run_set = ["pip_audit"]
    changes = [_make_change("write", "src/app.js")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    # pip_audit is relevant to requirements.txt/setup.py etc., not .js
    # Since scoped would be empty, safety kicks in → return original
    assert scoped == ["pip_audit"]
    assert result.removed_validators == []


# ---------------------------------------------------------------------------
# Test 16: ScopeResult contains correct audit data
# ---------------------------------------------------------------------------


def test_scope_result_metrics():
    """ScopeResult contains accurate audit info."""
    cfg = _make_config(enabled=True, mode="narrow")
    run_set = ["ruff", "pytest", "docker_build", "bandit"]
    changes = [
        _make_change("write", "src/main.py"),
        _make_change("patch", "src/utils.py"),
    ]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert result.original_validators == ["ruff", "pytest", "docker_build", "bandit"]
    assert set(result.scoped_validators) == {"ruff", "pytest", "bandit"}
    assert result.changed_files == ["src/main.py", "src/utils.py"]
    assert len(result.expanded_files) >= 2  # at least source files
    assert "docker_build" in result.removed_validators


# ---------------------------------------------------------------------------
# Test 17: test mapping keeps pytest in scope
# ---------------------------------------------------------------------------


def test_scope_with_test_mapping():
    """Source changes → test mapping adds test files → pytest stays relevant."""
    cfg = _make_config(enabled=True, mode="narrow", test_source_mapping=True)
    run_set = ["ruff", "pytest"]
    # Only source file changed — but test mapping adds tests/test_foo.py
    changes = [_make_change("write", "src/foo.py")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert "pytest" in scoped
    assert "ruff" in scoped
    # test_foo.py should be in expanded
    assert "tests/test_foo.py" in result.expanded_files


# ---------------------------------------------------------------------------
# Test 18: conservative mode — no filtering
# ---------------------------------------------------------------------------


def test_scope_conservative_mode():
    """Conservative mode → no validators removed."""
    cfg = _make_config(enabled=True, mode="conservative")
    run_set = ["ruff", "pytest", "docker_build"]
    changes = [_make_change("write", "src/main.py")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert scoped == run_set
    assert result.removed_validators == []


# ---------------------------------------------------------------------------
# Test 19: Dockerfile-only changes
# ---------------------------------------------------------------------------


def test_scope_dockerfile_only():
    """Dockerfile-only changes → Python validators removed."""
    cfg = _make_config(enabled=True, mode="narrow")
    run_set = ["ruff", "mypy", "pytest", "docker_build"]
    changes = [_make_change("write", "Dockerfile")]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert "docker_build" in scoped
    assert "ruff" not in scoped
    assert "mypy" not in scoped
    assert "pytest" not in scoped
    assert set(result.removed_validators) == {"ruff", "mypy", "pytest"}


# ---------------------------------------------------------------------------
# Test 20: Mixed files keep all relevant validators
# ---------------------------------------------------------------------------


def test_scope_mixed_files():
    """.py + Dockerfile → all relevant validators kept."""
    cfg = _make_config(enabled=True, mode="narrow")
    run_set = ["ruff", "pytest", "docker_build", "pip_audit"]
    changes = [
        _make_change("write", "src/main.py"),
        _make_change("write", "Dockerfile"),
    ]

    scoped, result = apply_smart_scope(run_set, changes, cfg)
    assert "ruff" in scoped
    assert "pytest" in scoped
    assert "docker_build" in scoped
    # pip_audit not relevant to .py or Dockerfile
    assert "pip_audit" not in scoped
    assert "pip_audit" in result.removed_validators
