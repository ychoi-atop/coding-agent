#!/usr/bin/env python3
"""Render the AV4 closure evidence bundle template.

AV4-014 scope:
- provide a reusable closure evidence packet template
- offer a lightweight renderer for deterministic closeout packet generation
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "templates" / "AV4_CLOSURE_EVIDENCE_BUNDLE.md.tmpl"


class TemplateRenderError(ValueError):
    """Raised when required template values are missing."""


def render_template(template: str, values: dict[str, str]) -> str:
    tokens = set(TOKEN_RE.findall(template))
    missing = sorted(token for token in tokens if token not in values)
    if missing:
        raise TemplateRenderError(f"missing template value(s): {', '.join(missing)}")

    rendered = template
    for token in sorted(tokens):
        rendered = re.sub(r"\{\{\s*" + re.escape(token) + r"\s*\}\}", values[token], rendered)

    unresolved = TOKEN_RE.findall(rendered)
    if unresolved:
        unresolved_unique = ", ".join(sorted(set(unresolved)))
        raise TemplateRenderError(f"unresolved template token(s) remained: {unresolved_unique}")

    return rendered


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="Template file path")
    p.add_argument("--output", required=True, help="Rendered markdown output path")

    p.add_argument("--closure-status", default="✅ Closed on `main`")
    p.add_argument("--wave-scope", default="AV4-001 ~ AV4-014")
    p.add_argument("--closed-at-kst", required=True)

    p.add_argument("--docs-gate-status", default="✅ PASS")
    p.add_argument("--docs-gate-evidence", required=True)
    p.add_argument("--tests-gate-status", default="✅ PASS")
    p.add_argument("--tests-gate-evidence", required=True)
    p.add_argument("--status-hook-gate-status", default="✅ PASS")
    p.add_argument("--status-hook-gate-evidence", required=True)
    p.add_argument("--closure-doc-gate-status", default="✅ PASS")
    p.add_argument("--closure-doc-gate-evidence", default="docs/AUTONOMOUS_V4_WAVE_CLOSURE.md")

    p.add_argument("--closure-pr", required=True)
    p.add_argument("--closure-branch", required=True)
    p.add_argument("--closure-commit", required=True)
    p.add_argument("--status-hook-event", default="av4.closed")
    p.add_argument("--status-hook-audit-entry", required=True)

    p.add_argument("--risks-and-followups", default="- None")
    p.add_argument("--owner", required=True)
    p.add_argument("--reviewers", default="TODO")
    p.add_argument("--final-decision", default="GO")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    template_path = Path(args.template).resolve()
    if not template_path.exists():
        raise SystemExit(f"template not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8")

    values = {
        "closure_status": args.closure_status,
        "wave_scope": args.wave_scope,
        "closed_at_kst": args.closed_at_kst,
        "docs_gate_status": args.docs_gate_status,
        "docs_gate_evidence": args.docs_gate_evidence,
        "tests_gate_status": args.tests_gate_status,
        "tests_gate_evidence": args.tests_gate_evidence,
        "status_hook_gate_status": args.status_hook_gate_status,
        "status_hook_gate_evidence": args.status_hook_gate_evidence,
        "closure_doc_gate_status": args.closure_doc_gate_status,
        "closure_doc_gate_evidence": args.closure_doc_gate_evidence,
        "closure_pr": args.closure_pr,
        "closure_branch": args.closure_branch,
        "closure_commit": args.closure_commit,
        "status_hook_event": args.status_hook_event,
        "status_hook_audit_entry": args.status_hook_audit_entry,
        "risks_and_followups": args.risks_and_followups,
        "owner": args.owner,
        "reviewers": args.reviewers,
        "final_decision": args.final_decision,
    }

    rendered = render_template(template_text, values)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    print(f"[PASS] Rendered AV4 closure evidence bundle: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
