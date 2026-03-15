from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent.parent


def test_local_simple_e2e_smoke_script_covers_trust_compare_and_saved_snapshots(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/local_simple_e2e_smoke.py",
            "--artifacts-dir",
            str(artifacts_dir),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[NXT-007 smoke] PASS" in proc.stdout

    runs = sorted([p for p in artifacts_dir.iterdir() if p.is_dir()])
    assert runs, f"no smoke artifact run directory created under {artifacts_dir}"

    latest = runs[-1]
    result = json.loads((latest / "result.json").read_text(encoding="utf-8"))
    snapshots = json.loads((latest / "snapshots.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["run_id"]
    assert result["baseline_run_id"] == "run-smoke-baseline"
    assert result["compare_snapshot_id"]

    assert snapshots["static_index"]["has_trust_trend_cards"] is True
    assert snapshots["static_index"]["has_saved_comparisons_panel"] is True
    assert snapshots["static_index"]["has_trust_diff_filter"] is True

    trust_latest = snapshots["trust_latest"]
    assert trust_latest["empty"] is False
    assert trust_latest["summary"]["trust_status"] in {"high", "moderate", "low", "unknown"}

    trust_trends = snapshots["trust_trends"]
    assert trust_trends["summary"]["runs_considered"] >= 2
    assert len(trust_trends["runs"]) >= 2

    compare_payload = snapshots["compare"]
    assert compare_payload["left"]["run_id"] == "run-smoke-baseline"
    assert compare_payload["right"]["run_id"] == result["run_id"]
    assert compare_payload["delta"]["trust_status_changed"] is True

    compare_save = snapshots["compare_snapshot_save"]
    snapshot_id = compare_save["snapshot"]["snapshot_id"]
    assert snapshot_id == result["compare_snapshot_id"]

    compare_detail = snapshots["compare_snapshot_detail"]
    assert compare_detail["snapshot"]["snapshot_id"] == snapshot_id
    assert compare_detail["compare_payload"]["right"]["run_id"] == result["run_id"]

    compare_update = snapshots["compare_snapshot_update"]
    assert compare_update["snapshot"]["display_name"] == "Smoke compare snapshot"
    assert compare_update["snapshot"]["pinned"] is True
    assert compare_update["snapshot"]["tags"] == ["smoke", "trust"]

    compare_delete = snapshots["compare_snapshot_delete"]
    assert compare_delete["deleted"] is True
    assert compare_delete["snapshot_id"] == snapshot_id

    rows_after_delete = snapshots["compare_snapshot_list_after_delete"]["snapshots"]
    assert snapshot_id not in {row["snapshot_id"] for row in rows_after_delete}
