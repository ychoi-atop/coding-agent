from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "render_av4_closure_evidence.py"


def _load_module():
    script_path = _script_path()
    spec = importlib.util.spec_from_file_location("render_av4_closure_evidence", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_render_template_replaces_all_tokens() -> None:
    mod = _load_module()

    template = "Status: {{ status }}\nOwner: {{owner}}\n"
    rendered = mod.render_template(template, {"status": "done", "owner": "ops"})

    assert "{{" not in rendered
    assert "Status: done" in rendered
    assert "Owner: ops" in rendered


def test_main_renders_template_file_to_output(tmp_path: Path) -> None:
    mod = _load_module()

    output_path = tmp_path / "bundle.md"
    template_path = Path(__file__).resolve().parents[2] / "docs" / "templates" / "AV4_CLOSURE_EVIDENCE_BUNDLE.md.tmpl"

    rc = mod.main(
        [
            "--template",
            str(template_path),
            "--output",
            str(output_path),
            "--closed-at-kst",
            "2026-03-09 10:00 KST",
            "--docs-gate-evidence",
            "artifacts/docs-check/20260309.txt",
            "--tests-gate-evidence",
            "artifacts/tests/av4-014.log",
            "--status-hook-gate-evidence",
            "artifacts/status-hooks/status-hook-audit.jsonl#L21",
            "--closure-pr",
            "https://github.com/ychoi-atop/coding-agent/pull/999",
            "--closure-branch",
            "feat/av4-014-closure-evidence-template",
            "--closure-commit",
            "abcdef123456",
            "--status-hook-audit-entry",
            "sha-deadbeefcafe",
            "--owner",
            "autodev",
        ]
    )

    assert rc == 0
    text = output_path.read_text(encoding="utf-8")
    assert "{{" not in text
    assert "Closure PR: https://github.com/ychoi-atop/coding-agent/pull/999" in text
    assert "Status-hook event: `av4.closed`" in text
    assert "Owner: autodev" in text
