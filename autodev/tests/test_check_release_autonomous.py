from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from autodev.autonomous_evidence_schema import AUTONOMOUS_EVIDENCE_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent.parent


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_valid_smoke_run(run_dir: Path, *, include_schema: bool = True, include_v3_extras: bool = True) -> None:
    result_payload = {
        "ok": True,
        "guard_reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
    }
    if include_schema:
        result_payload["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION

    snapshots_payload = {
        "state": {
            "preflight": {
                "status": "passed",
                "ok": True,
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
            },
            "decisions": [
                {
                    "iteration": 1,
                    "guard_decision": {
                        "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                    },
                }
            ],
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
    }

    if include_schema:
        snapshots_payload["state"]["preflight"]["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION
        snapshots_payload["gate_results"]["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION
        snapshots_payload["guard"]["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION
        snapshots_payload["summary_json"]["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION

    if include_v3_extras:
        snapshots_payload["report"] = {
            "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION if include_schema else None,
            "mode": "autonomous_v1",
            "ok": True,
            "run_id": "rid-1",
            "preflight": {"status": "passed"},
            "guard_decision": {
                "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
        }
        snapshots_payload["strategy_trace"] = {
            "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION if include_schema else None,
            "attempts": [{"iteration": 1, "strategy": {"name": "mixed"}}],
        }

    _write_json(run_dir / "result.json", result_payload)
    _write_json(run_dir / "snapshots.json", snapshots_payload)


def _run_checker(artifacts_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
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


def test_check_release_autonomous_passes_for_valid_v3_evidence(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts" / "autonomous-e2e-smoke"
    run_dir = artifacts_root / "20260308-120000"
    _make_valid_smoke_run(run_dir, include_schema=True, include_v3_extras=True)

    proc = _run_checker(artifacts_root)

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[AV3-002 release check] PASS" in proc.stdout


def test_check_release_autonomous_fails_when_required_schema_field_is_missing(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts" / "autonomous-e2e-smoke"
    run_dir = artifacts_root / "20260308-120001"
    _make_valid_smoke_run(run_dir, include_schema=True, include_v3_extras=True)

    snapshots_path = run_dir / "snapshots.json"
    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
    snapshots["summary_json"]["guard_decision"]["reason_code"] = ""
    snapshots_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = _run_checker(artifacts_root)

    assert proc.returncode == 1
    assert "[AV3-002 release check] FAIL" in proc.stdout
    assert "snapshots.summary_json.guard_decision.reason_code" in proc.stdout


def test_check_release_autonomous_accepts_legacy_av2_artifacts_with_warning(tmp_path: Path) -> None:
    artifacts_root = tmp_path / "artifacts" / "autonomous-e2e-smoke"
    run_dir = artifacts_root / "20260308-120002"
    _make_valid_smoke_run(run_dir, include_schema=False, include_v3_extras=False)

    proc = _run_checker(artifacts_root)

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[AV3-002 release check] PASS" in proc.stdout
    assert "legacy compatibility mode" in proc.stdout
