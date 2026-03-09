from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent.parent


def _run_checker(target_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/check_av4_backlog_schema.py",
            "--file",
            str(target_file),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_av4_backlog_schema_passes_for_repo_backlog() -> None:
    proc = _run_checker(ROOT / "docs" / "AUTONOMOUS_V4_BACKLOG.md")

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[PASS] AV4 backlog metadata schema check passed" in proc.stdout


def test_check_av4_backlog_schema_fails_on_invalid_priority(tmp_path: Path) -> None:
    md = tmp_path / "bad_backlog.md"
    md.write_text(
        "\n".join(
            [
                "| ID | Priority | Effort | Ticket | Definition of Done (DoD) | Test plan | PR split |",
                "|---|---|---:|---|---|---|---|",
                "| AV4-001 | PX | S | Example ticket | Example DoD | Example test | 1 PR |",
            ]
        ),
        encoding="utf-8",
    )

    proc = _run_checker(md)

    assert proc.returncode == 1
    assert "invalid Priority 'PX'" in proc.stdout


def test_check_av4_backlog_schema_fails_on_invalid_pr_split(tmp_path: Path) -> None:
    md = tmp_path / "bad_pr_split.md"
    md.write_text(
        "\n".join(
            [
                "| ID | Priority | Effort | Ticket | Definition of Done (DoD) | Test plan | PR split |",
                "|---|---|---:|---|---|---|---|",
                "| AV4-001 | P1 | S | Example ticket | Example DoD | Example test | one PR |",
            ]
        ),
        encoding="utf-8",
    )

    proc = _run_checker(md)

    assert proc.returncode == 1
    assert "invalid PR split 'one PR'" in proc.stdout
