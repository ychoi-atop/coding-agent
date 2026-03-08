from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent.parent


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_valid_smoke_run(run_dir: Path) -> None:
    _write_json(
        run_dir / "result.json",
        {
            "ok": True,
            "guard_reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
        },
    )
    _write_json(
        run_dir / "snapshots.json",
        {
            "state": {
                "preflight": {
                    "status": "passed",
                }
            },
            "gate_results": {
                "attempts": [
                    {
                        "iteration": 1,
                        "gate_results": {"passed": False},
                    }
                ]
            },
            "guard": {
                "latest": {
                    "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                }
            },
            "summary_json": {
                "preflight_status": "passed",
                "gate_counts": {"total": 1, "fail": 1, "pass": 0},
                "guard_decision": {
                    "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                },
            },
            "quality_gate_latest": {
                "empty": False,
                "summary": {
                    "preflight_status": "passed",
                    "guard_decision": {
                        "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                    },
                },
            },
        },
    )


def test_check_release_autonomous_passes_for_valid_evidence(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts" / "autonomous-e2e-smoke"
    run_dir = artifacts_root / "20260308-120000"
    _make_valid_smoke_run(run_dir)

    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/check_release_autonomous.py",
            "--artifacts-dir",
            str(artifacts_root),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[AV2-014 release check] PASS" in proc.stdout


def test_check_release_autonomous_fails_with_clear_reason_when_guard_missing(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts" / "autonomous-e2e-smoke"
    run_dir = artifacts_root / "20260308-120001"
    _make_valid_smoke_run(run_dir)

    snapshots_path = run_dir / "snapshots.json"
    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
    snapshots["guard"]["latest"]["reason_code"] = ""
    snapshots_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "scripts/check_release_autonomous.py",
            "--artifacts-dir",
            str(artifacts_root),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert "[AV2-014 release check] FAIL" in proc.stdout
    assert "snapshots.guard.latest.reason_code is missing or empty" in proc.stdout
