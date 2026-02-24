from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
AUDIT_SCRIPT = ROOT_DIR / "docs" / "ops" / "check_template_parity_audit.py"


def run_audit(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(AUDIT_SCRIPT), *args]
    return subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)


def test_template_parity_audit_passes_in_repo_root():
    result = run_audit()
    assert result.returncode == 0
    assert "Template parity + drift audit passed." in result.stdout


def test_template_parity_audit_has_help_text():
    result = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Check generated template parity" in result.stdout
