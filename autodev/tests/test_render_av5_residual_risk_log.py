from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "render_av5_residual_risk_log.py"


def _load_module():
    script_path = _script_path()
    spec = importlib.util.spec_from_file_location("render_av5_residual_risk_log", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_render_template_replaces_all_tokens() -> None:
    mod = _load_module()

    template = "Owner: {{ owner }}\nSeverity: {{severity}}\nMitigation: {{ mitigation }}\n"
    rendered = mod.render_template(template, {"owner": "ops", "severity": "high", "mitigation": "runbook"})

    assert "{{" not in rendered
    assert "Owner: ops" in rendered
    assert "Severity: high" in rendered
    assert "Mitigation: runbook" in rendered


def test_main_renders_template_file_to_output(tmp_path: Path) -> None:
    mod = _load_module()

    output_path = tmp_path / "av5-risk-log.md"
    template_path = Path(__file__).resolve().parents[2] / "docs" / "templates" / "AV5_RESIDUAL_RISK_LOG.md.tmpl"

    rc = mod.main(
        [
            "--template",
            str(template_path),
            "--output",
            str(output_path),
            "--snapshot-at-kst",
            "2026-03-09 11:55 KST",
            "--risk-rows",
            "| R-007 | Operator handoff ambiguity | autonomy-ops | High | Add explicit checklist + summary examples | unresolved handoff questions > 0 | 2026-03-10 09:00 KST | Open |",
            "--escalation-notes",
            "- Escalate to AV5-008 owner if risk remains High after next review",
            "--prepared-by",
            "autodev",
            "--reviewers",
            "ops-lead",
            "--decision",
            "Mitigation in progress",
        ]
    )

    assert rc == 0
    text = output_path.read_text(encoding="utf-8")
    assert "{{" not in text
    assert "| R-007 | Operator handoff ambiguity | autonomy-ops | High |" in text
    assert "- Prepared by: autodev" in text
    assert "- Decision: Mitigation in progress" in text
