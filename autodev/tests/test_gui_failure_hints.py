from __future__ import annotations

import json
from pathlib import Path

from autodev.gui_failure_hints import build_run_control_fix_hints


def test_build_run_control_fix_hints_missing_prd() -> None:
    hints = build_run_control_fix_hints(
        action="start",
        error={"code": "missing_prd", "message": "'prd' is required"},
        payload={},
    )
    assert hints
    assert any("PRD" in hint for hint in hints)


def test_build_run_control_fix_hints_forbidden_role_includes_policy_tip() -> None:
    hints = build_run_control_fix_hints(
        action="start",
        error={
            "code": "forbidden_role",
            "message": "Role 'evaluator' cannot perform 'start'.",
            "allowed_roles": ["operator", "developer"],
        },
        payload={},
    )
    assert any("operator" in hint and "developer" in hint for hint in hints)
    assert any("policy" in hint.lower() for hint in hints)


def test_build_run_control_fix_hints_resume_includes_validator_signal(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-001"
    ad = run_dir / ".autodev"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "task_final_last_validation.json").write_text(
        json.dumps(
            {
                "validation": [
                    {"name": "ruff", "status": "failed", "ok": False},
                    {"name": "pytest", "status": "passed", "ok": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    hints = build_run_control_fix_hints(
        action="resume",
        error={
            "code": "invalid_payload",
            "message": "resume target appears finalized (status is terminal); choose an in-progress run checkpoint",
        },
        payload={"out": str(run_dir)},
        runs_root=tmp_path,
    )

    assert any("Retry" in hint for hint in hints)
    assert any("validator" in hint.lower() and "ruff" in hint for hint in hints)
