from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "check_markdown_links.py"


def _load_module():
    script_path = _script_path()
    spec = importlib.util.spec_from_file_location("check_markdown_links", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_main_fails_on_missing_av4_inline_doc_reference(tmp_path: Path, capsys) -> None:
    mod = _load_module()

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "AUTONOMOUS_V4_BACKLOG.md").write_text(
        "# AUTONOMOUS V4\n\nCompanion plan: `docs/AUTONOMOUS_V4_WAVE_PLAN.md`\n",
        encoding="utf-8",
    )

    mod.REPO_ROOT = tmp_path
    mod.DOC_DIRS = [tmp_path / "docs"]
    mod.README_FILES = []

    rc = mod.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "AUTONOMOUS_V4_BACKLOG.md -> docs/AUTONOMOUS_V4_WAVE_PLAN.md" in out


def test_main_passes_when_av4_inline_doc_reference_exists(tmp_path: Path) -> None:
    mod = _load_module()

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "AUTONOMOUS_V4_WAVE_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (docs_dir / "AUTONOMOUS_V4_BACKLOG.md").write_text(
        "# AUTONOMOUS V4\n\nCompanion plan: `docs/AUTONOMOUS_V4_WAVE_PLAN.md`\n",
        encoding="utf-8",
    )

    mod.REPO_ROOT = tmp_path
    mod.DOC_DIRS = [tmp_path / "docs"]
    mod.README_FILES = []

    rc = mod.main()
    assert rc == 0
