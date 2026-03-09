#!/usr/bin/env python3
"""Validate AV5 backlog metadata table schema.

Enforces a strict docs table contract for:
- ID
- Priority
- Effort
- Status
- Ticket
- Definition of Done (DoD)
- Test plan
- PR split
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

EXPECTED_HEADERS = [
    "ID",
    "Priority",
    "Effort",
    "Status",
    "Ticket",
    "Definition of Done (DoD)",
    "Test plan",
    "PR split",
]

ID_RE = re.compile(r"^AV5-\d{3}$")
PRIORITY_RE = re.compile(r"^P[0-3]$")
EFFORT_RE = re.compile(r"^(S|M|L)$")
PR_SPLIT_RE = re.compile(r"^\d+\s+PRs?(?:\s+\(.+\))?$")


@dataclass
class ValidationError:
    line: int
    message: str


def _parse_table_rows(lines: list[str]) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped.startswith("|"):
            continue
        if not stripped.endswith("|"):
            rows.append((idx, []))
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        rows.append((idx, cells))
    return rows


def validate(path: Path) -> list[ValidationError]:
    errors: list[ValidationError] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = _parse_table_rows(lines)

    if len(rows) < 3:
        return [ValidationError(line=1, message="missing AV5 backlog metadata table")]

    header_line, headers = rows[0]
    if headers != EXPECTED_HEADERS:
        errors.append(
            ValidationError(
                line=header_line,
                message=(
                    "invalid backlog table headers; "
                    f"expected {EXPECTED_HEADERS}, got {headers}"
                ),
            )
        )

    sep_line, sep = rows[1]
    if len(sep) != len(EXPECTED_HEADERS) or any(not cell or set(cell) - {":", "-"} for cell in sep):
        errors.append(ValidationError(line=sep_line, message="invalid markdown separator row"))

    seen_ids: set[str] = set()
    for line_no, cells in rows[2:]:
        if len(cells) != len(EXPECTED_HEADERS):
            errors.append(
                ValidationError(
                    line=line_no,
                    message=f"expected {len(EXPECTED_HEADERS)} columns, found {len(cells)}",
                )
            )
            continue

        ticket_id, priority, effort, status, ticket, dod, test_plan, pr_split = cells

        if not ID_RE.match(ticket_id):
            errors.append(ValidationError(line=line_no, message=f"invalid ID '{ticket_id}' (expected AV5-###)"))
        elif ticket_id in seen_ids:
            errors.append(ValidationError(line=line_no, message=f"duplicate ID '{ticket_id}'"))
        else:
            seen_ids.add(ticket_id)

        if not PRIORITY_RE.match(priority):
            errors.append(ValidationError(line=line_no, message=f"invalid Priority '{priority}' (expected P0-P3)"))
        if not EFFORT_RE.match(effort):
            errors.append(ValidationError(line=line_no, message=f"invalid Effort '{effort}' (expected S/M/L)"))

        if not status:
            errors.append(ValidationError(line=line_no, message="Status must be non-empty"))
        if not ticket:
            errors.append(ValidationError(line=line_no, message="Ticket must be non-empty"))
        if not dod:
            errors.append(ValidationError(line=line_no, message="Definition of Done (DoD) must be non-empty"))
        if not test_plan:
            errors.append(ValidationError(line=line_no, message="Test plan must be non-empty"))
        if not PR_SPLIT_RE.match(pr_split):
            errors.append(
                ValidationError(
                    line=line_no,
                    message=(
                        f"invalid PR split '{pr_split}' "
                        "(expected '<n> PR' / '<n> PRs' with optional note)"
                    ),
                )
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AV5 backlog metadata table schema")
    parser.add_argument(
        "--file",
        default="docs/AUTONOMOUS_V5_BACKLOG.md",
        help="Path to AV5 backlog markdown file",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[FAIL] backlog file not found: {path}")
        return 1

    errors = validate(path)
    if errors:
        print("[FAIL] AV5 backlog metadata schema check failed")
        for err in errors:
            print(f"  - {path}:{err.line}: {err.message}")
        return 1

    print("[PASS] AV5 backlog metadata schema check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
