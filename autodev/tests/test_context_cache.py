"""Tests for autodev.context_cache module."""

from __future__ import annotations

from unittest.mock import MagicMock

from autodev.context_cache import (
    CacheSavings,
    IncrementalContextCache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PY = """\
import os
from pathlib import Path

class Foo:
    \"\"\"A sample class.\"\"\"
    def bar(self):
        pass

def baz():
    return 42

CONSTANT = "hello"
"""


def _mock_code_index(files_meta: dict | None = None) -> MagicMock:
    """Build a mock CodeIndex with the given file metadata."""
    index = MagicMock()
    if files_meta is None:
        index.files = {}
        return index

    index.files = {}
    for path, (symbols, imports, lang) in files_meta.items():
        meta = MagicMock()
        sym_objects = []
        for s in symbols:
            sym = MagicMock()
            parts = s.split(" ", 1)
            sym.kind = parts[0] if len(parts) == 2 else "function"
            sym.name = parts[1] if len(parts) == 2 else parts[0]
            sym_objects.append(sym)
        meta.symbols = sym_objects
        meta.imports = imports
        meta.language = lang
        index.files[path] = meta
    return index


# ---------------------------------------------------------------------------
# Test 1: First iteration returns full content unchanged
# ---------------------------------------------------------------------------


def test_first_iteration_full_content():
    cache = IncrementalContextCache(enabled=True)
    ctx = {"a.py": SAMPLE_PY, "b.py": "x = 1\n"}

    result, savings = cache.record_and_transform("task-1", ctx)

    # First iteration: all files are "new", returned unchanged
    assert result == ctx
    assert savings.files_new == 2
    assert savings.files_unchanged == 0
    assert savings.files_changed == 0
    assert savings.chars_saved == 0


# ---------------------------------------------------------------------------
# Test 2: Second iteration with unchanged file returns stub
# ---------------------------------------------------------------------------


def test_second_iteration_unchanged_stub():
    cache = IncrementalContextCache(enabled=True)
    ctx = {"a.py": SAMPLE_PY}

    # First call
    result1, savings1 = cache.record_and_transform("task-1", ctx)
    assert result1["a.py"] == SAMPLE_PY

    # Second call — same content
    result2, savings2 = cache.record_and_transform("task-1", ctx)
    assert result2["a.py"] != SAMPLE_PY  # should be a stub
    assert "[unchanged" in result2["a.py"]
    assert savings2.files_unchanged == 1
    assert savings2.chars_saved > 0


# ---------------------------------------------------------------------------
# Test 3: Second iteration with changed file returns full content
# ---------------------------------------------------------------------------


def test_second_iteration_changed_full():
    cache = IncrementalContextCache(enabled=True)

    ctx1 = {"a.py": "version = 1\n"}
    cache.record_and_transform("task-1", ctx1)

    ctx2 = {"a.py": "version = 2\n"}
    result2, savings2 = cache.record_and_transform("task-1", ctx2)

    assert result2["a.py"] == "version = 2\n"
    assert savings2.files_changed == 1
    assert savings2.files_unchanged == 0
    assert savings2.chars_saved == 0


# ---------------------------------------------------------------------------
# Test 4: Mixed changed and unchanged
# ---------------------------------------------------------------------------


def test_mixed_changed_unchanged():
    cache = IncrementalContextCache(enabled=True)

    ctx = {"a.py": "stable\n", "b.py": "changing\n"}
    cache.record_and_transform("task-1", ctx)

    ctx2 = {"a.py": "stable\n", "b.py": "changed!\n"}
    result, savings = cache.record_and_transform("task-1", ctx2)

    # a.py unchanged → stub
    assert "[unchanged" in result["a.py"]
    # b.py changed → full content
    assert result["b.py"] == "changed!\n"
    assert savings.files_unchanged == 1
    assert savings.files_changed == 1


# ---------------------------------------------------------------------------
# Test 5: New file in second iteration
# ---------------------------------------------------------------------------


def test_new_file_in_second_iteration():
    cache = IncrementalContextCache(enabled=True)

    ctx1 = {"a.py": "existing\n"}
    cache.record_and_transform("task-1", ctx1)

    ctx2 = {"a.py": "existing\n", "c.py": "brand new\n"}
    result, savings = cache.record_and_transform("task-1", ctx2)

    # a.py unchanged → stub
    assert "[unchanged" in result["a.py"]
    # c.py new → full
    assert result["c.py"] == "brand new\n"
    assert savings.files_new == 1
    assert savings.files_unchanged == 1


# ---------------------------------------------------------------------------
# Test 6: File removed in second iteration
# ---------------------------------------------------------------------------


def test_file_removed_in_second_iteration():
    cache = IncrementalContextCache(enabled=True)

    ctx1 = {"a.py": "keep\n", "b.py": "drop\n"}
    cache.record_and_transform("task-1", ctx1)

    # b.py no longer in context
    ctx2 = {"a.py": "keep\n"}
    result, savings = cache.record_and_transform("task-1", ctx2)

    assert "b.py" not in result
    assert "[unchanged" in result["a.py"]
    assert savings.files_unchanged == 1
    assert savings.files_total == 1


# ---------------------------------------------------------------------------
# Test 7: Disabled cache passthrough
# ---------------------------------------------------------------------------


def test_disabled_cache_passthrough():
    cache = IncrementalContextCache(enabled=False)
    ctx = {"a.py": "content\n"}

    # Even after two identical calls, content is unchanged
    cache.record_and_transform("task-1", ctx)
    result, savings = cache.record_and_transform("task-1", ctx)

    assert result["a.py"] == "content\n"
    assert savings.chars_saved == 0


# ---------------------------------------------------------------------------
# Test 8: Stub format "hash_only"
# ---------------------------------------------------------------------------


def test_stub_format_hash_only():
    cache = IncrementalContextCache(enabled=True, stub_format="hash_only")
    ctx = {"a.py": "x = 1\ny = 2\n"}

    cache.record_and_transform("task-1", ctx)
    result, _ = cache.record_and_transform("task-1", ctx)

    stub = result["a.py"]
    assert "[unchanged" in stub
    assert "chars]" in stub
    # Should NOT contain "exports:" since it's hash_only
    assert "exports:" not in stub


# ---------------------------------------------------------------------------
# Test 9: Structural stub with CodeIndex symbols
# ---------------------------------------------------------------------------


def test_stub_format_structural_with_index():
    index = _mock_code_index(
        {
            "a.py": (
                ["class Foo", "function bar", "function baz"],
                ["os", "pathlib"],
                "python",
            )
        }
    )
    cache = IncrementalContextCache(
        code_index=index, enabled=True, stub_format="structural"
    )
    ctx = {"a.py": SAMPLE_PY}

    cache.record_and_transform("task-1", ctx)
    result, _ = cache.record_and_transform("task-1", ctx)

    stub = result["a.py"]
    assert "[unchanged since last iteration" in stub
    assert "python" in stub
    assert "exports:" in stub
    assert "class Foo" in stub
    assert "function bar" in stub
    assert "imports:" in stub
    assert "os" in stub
    assert "pathlib" in stub


# ---------------------------------------------------------------------------
# Test 10: invalidate_task resets history
# ---------------------------------------------------------------------------


def test_invalidate_task_resets():
    cache = IncrementalContextCache(enabled=True)
    ctx = {"a.py": "stable\n"}

    cache.record_and_transform("task-1", ctx)
    # After invalidation, next call treated as first iteration
    cache.invalidate_task("task-1")

    result, savings = cache.record_and_transform("task-1", ctx)
    assert result["a.py"] == "stable\n"  # full content, not stub
    assert savings.files_new == 1
    assert savings.files_unchanged == 0


# ---------------------------------------------------------------------------
# Test 11: Cumulative savings across tasks
# ---------------------------------------------------------------------------


def test_cumulative_savings():
    cache = IncrementalContextCache(enabled=True)

    # Use realistically-sized content so stubs are actually shorter
    big_content_a = "# module a\n" + "x = 1\n" * 200  # ~1200 chars
    big_content_b = "# module b\n" + "y = 2\n" * 200

    # Task 1: two iterations
    cache.record_and_transform("task-1", {"a.py": big_content_a})
    cache.record_and_transform("task-1", {"a.py": big_content_a})

    # Task 2: two iterations
    cache.record_and_transform("task-2", {"b.py": big_content_b})
    cache.record_and_transform("task-2", {"b.py": big_content_b})

    cumulative = cache.get_cumulative_savings()
    assert cumulative.files_unchanged == 2  # one from each task's 2nd iter
    assert cumulative.chars_saved > 0


# ---------------------------------------------------------------------------
# Test 12: Hash deterministic
# ---------------------------------------------------------------------------


def test_hash_deterministic():
    cache = IncrementalContextCache(enabled=True)
    h1 = cache._compute_hash("hello world")
    h2 = cache._compute_hash("hello world")
    h3 = cache._compute_hash("different")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # SHA-256 hex length


# ---------------------------------------------------------------------------
# Test 13: Third iteration compares against second (not first)
# ---------------------------------------------------------------------------


def test_third_iteration_cumulative():
    cache = IncrementalContextCache(enabled=True)

    # Iteration 1: original
    ctx1 = {"a.py": "v1\n"}
    cache.record_and_transform("task-1", ctx1)

    # Iteration 2: changed
    ctx2 = {"a.py": "v2\n"}
    result2, savings2 = cache.record_and_transform("task-1", ctx2)
    assert result2["a.py"] == "v2\n"  # changed → full
    assert savings2.files_changed == 1

    # Iteration 3: same as iteration 2
    ctx3 = {"a.py": "v2\n"}
    result3, savings3 = cache.record_and_transform("task-1", ctx3)
    assert "[unchanged" in result3["a.py"]  # unchanged vs iteration 2
    assert savings3.files_unchanged == 1


# ---------------------------------------------------------------------------
# Test 14: Separate tasks are independent
# ---------------------------------------------------------------------------


def test_separate_tasks_independent():
    cache = IncrementalContextCache(enabled=True)
    content = "shared\n"

    # Task A: two iterations
    cache.record_and_transform("task-A", {"x.py": content})
    result_a2, _ = cache.record_and_transform("task-A", {"x.py": content})
    assert "[unchanged" in result_a2["x.py"]

    # Task B: first iteration of same content — should be full (independent)
    result_b1, savings_b1 = cache.record_and_transform("task-B", {"x.py": content})
    assert result_b1["x.py"] == content
    assert savings_b1.files_new == 1
    assert savings_b1.files_unchanged == 0


# ---------------------------------------------------------------------------
# Test 15: Savings calculation accuracy
# ---------------------------------------------------------------------------


def test_savings_calculation():
    savings = CacheSavings(
        files_total=3,
        files_unchanged=2,
        files_changed=1,
        files_new=0,
        chars_original=10000,
        chars_actual=3000,
    )
    assert savings.chars_saved == 7000
    assert abs(savings.savings_pct - 70.0) < 0.01

    # Edge case: zero original
    empty = CacheSavings.empty()
    assert empty.chars_saved == 0
    assert empty.savings_pct == 0.0


# ---------------------------------------------------------------------------
# Test 16: Empty files_context
# ---------------------------------------------------------------------------


def test_empty_files_context():
    cache = IncrementalContextCache(enabled=True)
    result, savings = cache.record_and_transform("task-1", {})
    assert result == {}
    assert savings.chars_saved == 0


# ---------------------------------------------------------------------------
# Test 17: accumulate method
# ---------------------------------------------------------------------------


def test_cache_savings_accumulate():
    s1 = CacheSavings(
        files_total=2, files_unchanged=1, files_changed=1,
        files_new=0, chars_original=5000, chars_actual=3000,
    )
    s2 = CacheSavings(
        files_total=3, files_unchanged=2, files_changed=0,
        files_new=1, chars_original=8000, chars_actual=2000,
    )
    s1.accumulate(s2)
    assert s1.files_total == 5
    assert s1.files_unchanged == 3
    assert s1.files_changed == 1
    assert s1.files_new == 1
    assert s1.chars_original == 13000
    assert s1.chars_actual == 5000
    assert s1.chars_saved == 8000
