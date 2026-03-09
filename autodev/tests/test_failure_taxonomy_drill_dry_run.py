from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent.parent


def test_failure_taxonomy_drill_dry_run_passes_and_writes_artifacts(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/failure_taxonomy_drill_dry_run.py",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[AV5-008 taxonomy drill] PASS" in proc.stdout

    runs = sorted([p for p in artifacts_dir.iterdir() if p.is_dir()])
    assert runs, f"no dry-run artifacts created under {artifacts_dir}"

    latest = runs[-1]
    result = json.loads((latest / "result.json").read_text(encoding="utf-8"))
    checks = json.loads((latest / "checks.json").read_text(encoding="utf-8"))

    assert result["schema_version"] == "av5-008-failure-taxonomy-drill-v1"
    assert result["ok"] is True
    assert result["failed"] == 0
    assert sorted(result["lanes_covered"]) == ["auto_fix", "escalate", "manual"]

    assert checks
    assert all(item.get("ok") is True for item in checks)
