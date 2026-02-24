import pytest

from autodev.patch_utils import apply_unified_diff, parse_unified_diff, validate_unified_diff


def test_validate_unified_diff_accepts_markdown_fenced_patch():
    fenced = """```diff
@@ -1,1 +1,1
-foo
+bar
```"""
    validate_unified_diff(fenced)
    hunks = parse_unified_diff(fenced)
    assert len(hunks) == 1
    assert hunks[0].orig_start == 1
    assert hunks[0].orig_len == 1


def test_apply_unified_diff_dry_run_does_not_return_content_and_does_not_fail():
    out = apply_unified_diff("foo\n", """@@ -1,1 +1,1\n-foo\n+bar\n""", dry_run=True)
    assert out is None


def test_apply_unified_diff_dry_run_rejects_invalid_patch():
    with pytest.raises(ValueError, match="No hunks found"):
        apply_unified_diff("foo\n", "bad patch", dry_run=True)
