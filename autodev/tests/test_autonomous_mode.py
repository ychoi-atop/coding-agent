from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_mode as autonomous_mode  # noqa: E402


class _FakeClient:
    def __init__(self, **_kwargs):
        self.model = _kwargs.get("model", "fake-model")

    def usage_summary(self):
        return {"total_calls": 0, "total_input_tokens": 0, "total_output_tokens": 0}


def _write_cfg(tmp_path: Path, *, include_quality_gate_policy: bool = False) -> Path:
    gate_policy = """
    quality_gate_policy:
      tests:
        min_pass_rate: 0.9
      security:
        max_high_findings: 0
      performance:
        max_regression_pct: 5
""" if include_quality_gate_policy else ""

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
run:
  autonomous:
    max_iterations: 3
    time_budget_sec: 600{gate_policy}
""",
        encoding="utf-8",
    )
    return cfg


def _write_prd(tmp_path: Path) -> Path:
    prd = tmp_path / "prd.md"
    prd.write_text("# goal\n\nship it", encoding="utf-8")
    return prd


def test_autonomous_start_retries_until_success(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls: list[bool] = []
    outcomes = [False, True]

    async def _fake_run(*_args, **kwargs):
        calls.append(bool(kwargs.get("resume")))
        ok = outcomes.pop(0)
        return ok, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--out",
            str(out_root),
            "--config",
            str(cfg),
            "--profile",
            "minimal",
            "--max-iterations",
            "3",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dirs = sorted(out_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    metadata = json.loads((run_dir / ".autodev" / "run_metadata.json").read_text(encoding="utf-8"))
    assert "autonomous_quality_gate_policy" not in metadata
    assert state["status"] == "completed"
    assert state["phase"] == "completed"
    assert state["current_iteration"] == 2
    assert len(state["attempts"]) == 2
    assert calls == [False, True]

    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["iterations_total"] == 2


def test_autonomous_start_records_quality_gate_policy_in_metadata_and_state(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path, include_quality_gate_policy=True)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    async def _fake_run(*_args, **_kwargs):
        return True, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--out",
            str(out_root),
            "--config",
            str(cfg),
            "--profile",
            "minimal",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dir = sorted(out_root.iterdir())[0]
    metadata = json.loads((run_dir / ".autodev" / "run_metadata.json").read_text(encoding="utf-8"))
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))

    expected_policy = {
        "tests": {"min_pass_rate": 0.9},
        "security": {"max_high_findings": 0},
        "performance": {"max_regression_pct": 5.0},
    }
    assert metadata["autonomous_quality_gate_policy"] == expected_policy
    assert state["policy"]["quality_gate_policy"] == expected_policy


def test_autonomous_quality_gate_failure_triggers_retry_and_records_typed_reason(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path, include_quality_gate_policy=True)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                True,
                {"project": {}},
                {"tasks": []},
                [
                    {
                        "name": "py test",
                        "ok": None,
                        "status": "error",
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "tests failed",
                        "diagnostics": {},
                    }
                ],
            )
        return (
            True,
            {"project": {}},
            {"tasks": []},
            [
                {
                    "name": "pytest",
                    "ok": None,
                    "status": "pass",
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "diagnostics": {},
                }
            ],
        )

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--out",
            str(out_root),
            "--config",
            str(cfg),
            "--profile",
            "minimal",
            "--max-iterations",
            "3",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    report_md = (run_dir / "AUTONOMOUS_REPORT.md").read_text(encoding="utf-8")

    assert calls == 2
    assert state["status"] == "completed"
    assert state["current_iteration"] == 2
    assert len(state["attempts"]) == 2

    first = state["attempts"][0]
    assert first["ok"] is False
    assert first["reason"] == "quality_gate_failed"
    assert first["quality_gate_failed"] is True
    reasons = first["quality_gate_fail_reasons"]
    assert isinstance(reasons, list) and reasons
    assert reasons[0]["type"] == "quality_gate_failed"
    assert reasons[0]["taxonomy_version"] == "av2-003"
    assert reasons[0]["code"] == "tests.min_pass_rate_not_met"
    assert reasons[0]["category"] == "reliability"
    assert reasons[0]["severity"] == "blocking"
    assert reasons[0]["signal_source"] == "final_validation.pytest"

    second = state["attempts"][1]
    assert second["ok"] is True
    assert second["gate_results"]["passed"] is True

    assert report["iterations_gate_failed"] == 1
    assert report["gate_results"]["passed"] is True
    assert "gate_fail_codes=tests.min_pass_rate_not_met" in report_md


def test_autonomous_quality_gate_results_artifact_persisted(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path, include_quality_gate_policy=True)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    async def _fake_run(*_args, **_kwargs):
        return (
            True,
            {"project": {}},
            {"tasks": []},
            [
                {
                    "name": "pytest",
                    "ok": True,
                    "status": "passed",
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "diagnostics": {},
                }
            ],
        )

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--out",
            str(out_root),
            "--config",
            str(cfg),
            "--profile",
            "minimal",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dir = sorted(out_root.iterdir())[0]
    gate_artifact = json.loads((run_dir / ".autodev" / "autonomous_gate_results.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))

    assert gate_artifact["policy"]["tests"]["min_pass_rate"] == 0.9
    assert len(gate_artifact["attempts"]) == 1
    assert gate_artifact["attempts"][0]["gate_results"]["passed"] is True

    assert report["gate_results"]["passed"] is True
    assert report["iterations_gate_failed"] == 0


def test_autonomous_start_stops_at_max_iterations(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return False, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    with pytest.raises(SystemExit) as exc:
        autonomous_mode.cli(
            [
                "start",
                "--prd",
                str(prd),
                "--out",
                str(out_root),
                "--config",
                str(cfg),
                "--profile",
                "minimal",
                "--max-iterations",
                "2",
                "--workspace-allowlist",
                str(tmp_path),
            ]
        )

    assert exc.value.code == 1
    assert calls == 2

    run_dirs = sorted(out_root.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["failure_reason"] == "max_iterations_exceeded"
    assert state["current_iteration"] == 2
    assert len(state["attempts"]) == 2
