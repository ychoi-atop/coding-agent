from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent.parent


def test_autonomous_e2e_smoke_script_passes_and_persists_snapshots(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/autonomous_e2e_smoke.py",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[AV2-013 smoke] PASS" in proc.stdout

    runs = sorted([p for p in artifacts_dir.iterdir() if p.is_dir()])
    assert runs, f"no smoke artifact run directory created under {artifacts_dir}"

    latest = runs[-1]
    result = json.loads((latest / "result.json").read_text(encoding="utf-8"))
    snapshots = json.loads((latest / "snapshots.json").read_text(encoding="utf-8"))

    assert result["schema_version"] == "av3-002-v1"
    assert result["ok"] is True
    assert result["guard_reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"

    assert snapshots["schema_version"] == "av3-002-v1"
    assert snapshots["state"]["preflight"]["status"] == "passed"
    assert snapshots["report"]["schema_version"] == "av3-002-v1"
    assert snapshots["strategy_trace"]["schema_version"] == "av3-002-v1"
    assert snapshots["summary_json"]["preflight_status"] == "passed"
    assert snapshots["summary_json"]["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"

    endpoint_payload = snapshots["quality_gate_latest"]
    assert endpoint_payload["empty"] is False
    assert endpoint_payload["summary"]["preflight_status"] == "passed"
    assert endpoint_payload["summary"]["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
