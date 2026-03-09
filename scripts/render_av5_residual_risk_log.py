#!/usr/bin/env python3
"""Render the AV5 residual-risk log template.

AV5-007 scope:
- provide a reusable AV5 residual-risk log template
- enforce owner/severity/mitigation sections through deterministic rendering inputs
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "docs" / "templates" / "AV5_RESIDUAL_RISK_LOG.md.tmpl"


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

    p.add_argument("--status", default="🚧 Active")
    p.add_argument("--wave-scope", default="AV5-001 ~ AV5-014")
    p.add_argument("--snapshot-at-kst", required=True)

    p.add_argument(
        "--risk-rows",
        default=(
            "| R-001 | Deterministic replay edge-case remains for multi-stage timeout conflicts | core/autonomous | "
            "Medium | Keep deterministic retry policy v2 with explicit cutoff + incident packet fallback | "
            "replay mismatch count > 0 for same run hash | 2026-03-16 10:00 KST | Open |"
        ),
    )
    p.add_argument("--escalation-notes", default="- None")

    p.add_argument("--prepared-by", required=True)
    p.add_argument("--reviewers", default="TODO")
    p.add_argument("--decision", default="Track + mitigate")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    template_path = Path(args.template).resolve()
    if not template_path.exists():
        raise SystemExit(f"template not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8")

    values = {
        "status": args.status,
        "wave_scope": args.wave_scope,
        "snapshot_at_kst": args.snapshot_at_kst,
        "risk_rows": args.risk_rows,
        "escalation_notes": args.escalation_notes,
        "prepared_by": args.prepared_by,
        "reviewers": args.reviewers,
        "decision": args.decision,
    }

    rendered = render_template(template_text, values)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    print(f"[PASS] Rendered AV5 residual-risk log: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
