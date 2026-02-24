from pathlib import Path

import pytest

from autodev.workspace import Change, Workspace


def test_workspace_patch_change_requires_valid_unified_diff_for_existing_file(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_text("app.py", "a = 1\n")

    valid_patch = """@@ -1,1 +1,1
-a = 1
+a = 2
"""
    ws.apply_changes([Change(op="patch", path="app.py", content=valid_patch)])

    assert ws.read_text("app.py") == "a = 2\n"


def test_workspace_patch_rejects_invalid_patch_for_existing_file(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_text("app.py", "a = 1\n")

    invalid_patch = "just plain text replacement"
    with pytest.raises(ValueError, match="No hunks found"):
        ws.apply_changes([Change(op="patch", path="app.py", content=invalid_patch)])


def test_workspace_patch_allows_full_replace_for_missing_file(tmp_path: Path):
    ws = Workspace(tmp_path)
    full_text = "hello\nworld\n"

    ws.apply_changes([Change(op="patch", path="new.txt", content=full_text)])
    assert ws.read_text("new.txt") == full_text


def test_workspace_apply_changes_supports_dry_run(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_text("app.py", "a = 1\n")

    valid_patch = """@@ -1,1 +1,1
-a = 1
+a = 2
"""
    ws.apply_changes([Change(op="patch", path="app.py", content=valid_patch)], dry_run=True)

    assert ws.read_text("app.py") == "a = 1\n"


def test_workspace_apply_changes_rolls_back_partial_apply_on_failure(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.write_text("before.txt", "keep\n")

    good_patch = """@@ -1,1 +1,1
-keep
+safe
"""
    bad_patch = "not a unified diff"

    with pytest.raises(ValueError, match="No hunks found"):
        ws.apply_changes(
            [
                Change(op="write", path="touch.txt", content="created\n"),
                Change(op="patch", path="before.txt", content=good_patch),
                Change(op="patch", path="before.txt", content=bad_patch),
            ]
        )

    assert not ws.exists("touch.txt")
    assert ws.read_text("before.txt") == "keep\n"


def test_workspace_apply_changes_skips_unchanged_write(tmp_path: Path, monkeypatch):
    ws = Workspace(tmp_path)
    ws.write_text("app.py", "a = 1\n")

    writes = {"count": 0}
    original_write_text = ws.write_text

    def tracked_write_text(path: str, content: str) -> None:
        writes["count"] += 1
        return original_write_text(path, content)

    monkeypatch.setattr(ws, "write_text", tracked_write_text)

    ws.apply_changes([Change(op="write", path="app.py", content="a = 1\n")])

    assert writes["count"] == 0
