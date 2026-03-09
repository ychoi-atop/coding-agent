#!/usr/bin/env python3
"""Validate AV5 carryover policy sample annotation contract."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ANNOTATION_RE = re.compile(
    r"\[CARRYOVER\]\[AV5->AV6\]\s+"
    r"source=(AV5-\d{3})\s+"
    r"target=(AV6-\d{3})\s+"
    r"status=deferred\s+"
    r"reason=\"[^\"]+\"\s+"
    r"owner=\S+"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AV5 carryover policy sample annotation")
    parser.add_argument(
        "--file",
        default="docs/AUTONOMOUS_V5_CARRYOVER_POLICY.md",
        help="Path to AV5 carryover policy markdown file",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[FAIL] carryover policy file not found: {path}")
        return 1

    text = path.read_text(encoding="utf-8")

    if "## Sample carryover entry (reference)" not in text:
        print("[FAIL] missing sample carryover entry section")
        return 1

    matches = ANNOTATION_RE.findall(text)
    if not matches:
        print("[FAIL] no valid AV5->AV6 carryover annotation sample found")
        return 1

    print("[PASS] AV5 carryover policy sample annotation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
