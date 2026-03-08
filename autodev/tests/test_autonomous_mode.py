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


def _write_cfg(
    tmp_path: Path,
    *,
    include_quality_gate_policy: bool = False,
    stop_guard_policy_yaml: str = "",
    preflight_yaml: str = "",
    budget_guard_policy_yaml: str = "",
) -> Path:
    gate_policy = """
    quality_gate_policy:
      tests:
        min_pass_rate: 0.9
      security:
        max_high_findings: 0
      performance:
        max_regression_pct: 5
""" if include_quality_gate_policy else ""

    stop_guard_policy = ""
    if stop_guard_policy_yaml.strip():
        stop_guard_policy = f"\n    stop_guard_policy:\n{stop_guard_policy_yaml.rstrip()}"

    preflight_policy = ""
    if preflight_yaml.strip():
        preflight_policy = f"\n    preflight:\n{preflight_yaml.rstrip()}"

    budget_guard_policy = ""
    if budget_guard_policy_yaml.strip():
        budget_guard_policy = f"\n    budget_guard_policy:\n{budget_guard_policy_yaml.rstrip()}"

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
    time_budget_sec: 600{gate_policy}{stop_guard_policy}{preflight_policy}{budget_guard_policy}
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
    assert state["preflight"]["status"] == "passed"
    assert state["preflight"]["reason_codes"] == []
    assert calls == [False, True]

    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["iterations_total"] == 2
    assert report["preflight"]["status"] == "passed"


def test_autonomous_start_preflight_fails_early_on_blocked_path(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return True, {"project": {}}, {"tasks": []}, []

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
                "--workspace-allowlist",
                str(tmp_path),
                "--blocked-paths",
                str(tmp_path),
            ]
        )

    assert exc.value.code == 1
    assert calls == 0

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert state["failure_reason"] == "preflight_failed"
    assert state["preflight"]["status"] == "failed"
    assert "autonomous_preflight.path_blocked" in state["preflight"]["reason_codes"]
    assert report["preflight"]["status"] == "failed"


def test_autonomous_start_preflight_fails_early_on_missing_prd(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    missing_prd = tmp_path / "missing-prd.md"
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return True, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    with pytest.raises(SystemExit) as exc:
        autonomous_mode.cli(
            [
                "start",
                "--prd",
                str(missing_prd),
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

    assert exc.value.code == 1
    assert calls == 0

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert state["failure_reason"] == "preflight_failed"
    assert state["preflight"]["status"] == "failed"
    assert "autonomous_preflight.required_file_missing" in state["preflight"]["reason_codes"]


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


def test_evaluate_quality_gates_baseline_absent_preserves_threshold_behavior_and_persists_artifact(tmp_path):
    ws = autonomous_mode.Workspace(tmp_path)
    policy = autonomous_mode.AutonomousQualityGatePolicy(
        tests=autonomous_mode.AutonomousTestsGateThresholds(min_pass_rate=0.9),
        security=autonomous_mode.AutonomousSecurityGateThresholds(max_high_findings=0),
        performance=autonomous_mode.AutonomousPerformanceGateThresholds(max_regression_pct=5.0),
    )

    result = autonomous_mode._evaluate_quality_gates(
        ws=ws,
        policy=policy,
        last_validation=[
            {"name": "pytest", "status": "passed", "returncode": 0, "diagnostics": {}},
            {"name": "bandit", "status": "passed", "returncode": 0, "diagnostics": {"high_findings": 0}},
            {"name": "perf", "status": "passed", "diagnostics": {"regression_pct": 4.0}},
        ],
    )

    assert result["passed"] is True
    perf_reasons = [r for r in result["fail_reasons"] if r.get("gate") == "performance"]
    assert perf_reasons == []

    baseline = json.loads((tmp_path / ".autodev" / "autonomous_gate_baseline.json").read_text(encoding="utf-8"))
    assert baseline["version"] == 1
    assert baseline["gates"]["performance"]["metric"] == "regression_pct"
    assert len(baseline["gates"]["performance"]["observations"]) == 1


def test_evaluate_quality_gates_detects_baseline_regression_with_typed_reason_code(tmp_path):
    ws = autonomous_mode.Workspace(tmp_path)
    policy = autonomous_mode.AutonomousQualityGatePolicy(
        tests=None,
        security=None,
        performance=autonomous_mode.AutonomousPerformanceGateThresholds(max_regression_pct=10.0),
    )

    for observed in (1.0, 1.2):
        warmup = autonomous_mode._evaluate_quality_gates(
            ws=ws,
            policy=policy,
            last_validation=[
                {"name": "perf", "status": "passed", "diagnostics": {"regression_pct": observed}},
            ],
        )
        assert warmup["passed"] is True

    regressed = autonomous_mode._evaluate_quality_gates(
        ws=ws,
        policy=policy,
        last_validation=[
            {"name": "perf", "status": "passed", "diagnostics": {"regression_pct": 3.5}},
        ],
    )

    assert regressed["passed"] is False
    codes = [r.get("code") for r in regressed["fail_reasons"] if isinstance(r, dict)]
    assert "performance.baseline_regression_detected" in codes
    assert "performance.max_regression_pct_exceeded" not in codes

    perf_gate = regressed["gates"]["performance"]
    assert perf_gate["status"] == "failed"
    assert perf_gate["baseline"]["sample_size"] == 2
    assert perf_gate["baseline"]["baseline_regression_limit_pct"] is not None

    baseline = json.loads((tmp_path / ".autodev" / "autonomous_gate_baseline.json").read_text(encoding="utf-8"))
    assert len(baseline["gates"]["performance"]["observations"]) == 3


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


def test_autonomous_start_budget_guard_time_budget_emits_typed_reason(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return False, {"project": {}}, {"tasks": []}, []

    class _FakeClock:
        def __init__(self):
            self._values = [1000.0, 1000.0, 1003.0, 1003.0, 1003.0]
            self._index = 0

        def __call__(self):
            value = self._values[min(self._index, len(self._values) - 1)]
            self._index += 1
            return value

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)
    monkeypatch.setattr(autonomous_mode.time, "monotonic", _FakeClock())

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
                "5",
                "--time-budget-sec",
                "2",
                "--workspace-allowlist",
                str(tmp_path),
            ]
        )

    assert exc.value.code == 1
    assert calls == 1

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))

    assert state["failure_reason"] == "time_budget_exceeded"
    assert state["budget_guard"]["status"] == "triggered"
    assert state["budget_guard"]["decision"]["reason_code"] == "autonomous_budget_guard.max_wall_clock_seconds_exceeded"
    assert report["budget_guard"]["decision"]["reason_code"] == "autonomous_budget_guard.max_wall_clock_seconds_exceeded"


def test_autonomous_start_budget_guard_non_trigger_includes_token_placeholder_diag(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
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
            "--max-estimated-token-budget",
            "1000",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    metadata = json.loads((run_dir / ".autodev" / "run_metadata.json").read_text(encoding="utf-8"))

    assert state["budget_guard"]["status"] == "within_budget"
    assert state["budget_guard"]["decision"] is None
    assert state["budget_guard"]["checks"]["estimated_tokens"]["status"] == "not_available"

    reason_codes = [
        item.get("reason_code")
        for item in state["budget_guard"].get("diagnostics", [])
        if isinstance(item, dict)
    ]
    assert "autonomous_budget_guard.estimated_token_budget_not_available" in reason_codes
    assert report["budget_guard"]["checks"]["estimated_tokens"]["status"] == "not_available"
    assert metadata["autonomous_budget_guard_policy"]["max_estimated_token_budget"] == 1000


def test_route_strategy_from_fail_reasons_maps_single_and_mixed_domains() -> None:
    tests_only = autonomous_mode._route_strategy_from_fail_reasons(
        [
            {
                "code": "tests.min_pass_rate_not_met",
                "category": "reliability",
                "gate": "tests",
            }
        ]
    )
    assert tests_only["recommended"] == "tests-focused"
    assert tests_only["gate_fail_codes"] == ["tests.min_pass_rate_not_met"]

    mixed = autonomous_mode._route_strategy_from_fail_reasons(
        [
            {"code": "tests.min_pass_rate_not_met", "category": "reliability", "gate": "tests"},
            {"code": "security.max_high_findings_exceeded", "category": "security", "gate": "security"},
        ]
    )
    assert mixed["recommended"] == "mixed"
    assert "tests.min_pass_rate_not_met" in mixed["gate_fail_codes"]
    assert "security.max_high_findings_exceeded" in mixed["gate_fail_codes"]


def test_resolve_operator_guidance_entry_exact_and_unknown_fallback() -> None:
    mapped = autonomous_mode._resolve_operator_guidance_entry("autonomous_preflight.path_blocked")
    assert mapped["family"] == "preflight"
    assert mapped["source"] == "exact"
    assert mapped["playbook_url"].endswith("#preflight-failures")

    unknown = autonomous_mode._resolve_operator_guidance_entry("custom.operator_code")
    assert unknown["family"] == "unknown"
    assert unknown["source"] == "generic_fallback"
    assert unknown["playbook_url"].endswith("#unknown-or-unmapped-codes")


def test_render_report_includes_operator_guidance_payload_and_markdown_section() -> None:
    state = {
        "run_id": "run-guidance",
        "request_id": "req-guidance",
        "run_out": "/tmp/run-guidance",
        "profile": "minimal",
        "attempts": [
            {
                "iteration": 1,
                "ok": False,
                "resume": False,
                "reason": "quality_gate_failed",
                "gate_results": {
                    "passed": False,
                    "fail_reasons": [
                        {"code": "tests.min_pass_rate_not_met"},
                        {"code": "custom.operator_code"},
                    ],
                },
                "guard_decision": {
                    "decision": "stop",
                    "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
                },
            }
        ],
        "preflight": {
            "status": "failed",
            "reason_codes": ["autonomous_preflight.path_blocked"],
            "diagnostics": [],
        },
        "budget_guard": {
            "status": "triggered",
            "decision": {
                "decision": "stop",
                "reason_code": "autonomous_budget_guard.max_autonomous_iterations_reached",
            },
            "diagnostics": [],
        },
    }

    report, report_md = autonomous_mode._render_report(state, ok=False, last_validation=[])

    guidance = report["operator_guidance"]
    assert guidance["taxonomy_version"] == "av2-011"
    assert any(item["code"] == "tests.min_pass_rate_not_met" for item in guidance["resolved"])
    assert any(
        item["code"] == "custom.operator_code" and item["source"] == "generic_fallback"
        for item in guidance["resolved"]
    )

    assert "## Operator Guidance" in report_md
    assert "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md#gate-failures" in report_md


def test_resolve_retry_strategy_rotates_after_no_improvement_on_same_strategy() -> None:
    fail_reasons = [
        {
            "code": "tests.min_pass_rate_not_met",
            "category": "reliability",
            "gate": "tests",
        }
    ]
    gate_results = {
        "passed": False,
        "gates": {"tests": {"status": "failed"}},
        "fail_reasons": fail_reasons,
    }
    attempts = [
        {
            "iteration": 1,
            "strategy": {"name": "tests-focused"},
            "gate_results": gate_results,
        },
        {
            "iteration": 2,
            "strategy": {"name": "tests-focused"},
            "gate_results": gate_results,
        },
    ]

    routed = autonomous_mode._resolve_retry_strategy(attempts, iteration=3)
    assert routed["recommended"] == "tests-focused"
    assert routed["name"] != "tests-focused"
    assert routed["rotation_applied"] is True
    assert routed["rotation_reason"] == "prior_same_strategy_no_measurable_gate_improvement"


def test_autonomous_stop_guard_repeated_gate_failure_triggers_early_stop_and_persists_artifacts(tmp_path, monkeypatch):
    cfg = _write_cfg(
        tmp_path,
        include_quality_gate_policy=True,
        stop_guard_policy_yaml="""
      max_consecutive_gate_failures: 2
      max_consecutive_no_improvement: 5
      rollback_recommendation_enabled: true
""",
    )
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return (
            True,
            {"project": {}},
            {"tasks": []},
            [
                {
                    "name": "pytest",
                    "ok": False,
                    "status": "failed",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "tests failed",
                    "diagnostics": {},
                }
            ],
        )

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
                "5",
                "--workspace-allowlist",
                str(tmp_path),
            ]
        )

    assert exc.value.code == 1
    assert calls == 2

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    guard_artifact = json.loads((run_dir / ".autodev" / "autonomous_guard_decisions.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert state["failure_reason"] == "autonomous_guard_stop"
    assert state["current_iteration"] == 2
    assert len(state["attempts"]) == 2

    guard_decision = state["attempts"][1]["guard_decision"]
    assert guard_decision["decision"] == "stop"
    assert guard_decision["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert guard_decision["rollback_recommended"] is True

    assert report["guard_decision"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert report["guard_decisions_total"] == 1

    assert guard_artifact["latest"]["reason_code"] == "autonomous_guard.repeated_gate_failure_limit_reached"
    assert len(guard_artifact["decisions"]) == 1


def test_evaluate_stop_guard_decision_triggers_on_no_improvement_pattern() -> None:
    fail_reasons = [{"code": "tests.min_pass_rate_not_met"}]
    gate_results = {
        "passed": False,
        "gates": {"tests": {"status": "failed"}},
        "fail_reasons": fail_reasons,
    }
    attempts = [
        {"iteration": 1, "gate_results": gate_results},
        {"iteration": 2, "gate_results": gate_results},
        {"iteration": 3, "gate_results": gate_results},
    ]

    decision = autonomous_mode._evaluate_stop_guard_decision(
        attempts,
        autonomous_mode.AutonomousStopGuardPolicy(
            max_consecutive_gate_failures=5,
            max_consecutive_no_improvement=2,
            rollback_recommendation_enabled=True,
        ),
    )

    assert isinstance(decision, dict)
    assert decision["decision"] == "stop"
    assert decision["reason_code"] == "autonomous_guard.no_measurable_gate_improvement_limit_reached"
    assert decision["rollback_recommended"] is True


def test_evaluate_stop_guard_decision_non_trigger_when_gate_improves() -> None:
    attempts = [
        {
            "iteration": 1,
            "gate_results": {
                "passed": False,
                "gates": {"tests": {"status": "failed"}, "security": {"status": "failed"}},
                "fail_reasons": [
                    {"code": "tests.min_pass_rate_not_met"},
                    {"code": "security.max_high_findings_exceeded"},
                ],
            },
        },
        {
            "iteration": 2,
            "gate_results": {
                "passed": False,
                "gates": {"tests": {"status": "failed"}},
                "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
            },
        },
        {
            "iteration": 3,
            "gate_results": {
                "passed": False,
                "gates": {"tests": {"status": "failed"}},
                "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}],
            },
        },
    ]

    decision = autonomous_mode._evaluate_stop_guard_decision(
        attempts,
        autonomous_mode.AutonomousStopGuardPolicy(
            max_consecutive_gate_failures=5,
            max_consecutive_no_improvement=2,
            rollback_recommendation_enabled=True,
        ),
    )

    assert decision is None


def test_autonomous_strategy_trace_artifact_persisted_and_report_state_include_latest_strategy(tmp_path, monkeypatch):
    cfg = _write_cfg(
        tmp_path,
        include_quality_gate_policy=True,
        stop_guard_policy_yaml="""
      max_consecutive_gate_failures: 10
      max_consecutive_no_improvement: 10
""",
    )
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    calls = 0

    async def _fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return (
                True,
                {"project": {}},
                {"tasks": []},
                [
                    {
                        "name": "pytest",
                        "ok": False,
                        "status": "failed",
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
            "--max-iterations",
            "4",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    run_dir = sorted(out_root.iterdir())[0]
    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))
    strategy_trace = json.loads((run_dir / ".autodev" / "autonomous_strategy_trace.json").read_text(encoding="utf-8"))

    assert calls == 4
    assert state["status"] == "completed"
    assert len(state["attempts"]) == 4
    assert state["attempts"][0]["strategy"]["name"] == "mixed"
    assert state["attempts"][1]["strategy"]["name"] == "tests-focused"
    assert state["attempts"][2]["strategy"]["name"] == "tests-focused"
    assert state["attempts"][3]["strategy"]["rotation_applied"] is True
    assert state["attempts"][3]["strategy"]["name"] == "security-focused"

    assert strategy_trace["latest"]["name"] == "security-focused"
    assert len(strategy_trace["attempts"]) == 4
    assert report["latest_strategy"]["name"] == "security-focused"
    assert state["last_strategy"]["name"] == "security-focused"


def test_autonomous_resume_state_clean_path_preserves_attempt_history(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    outcomes = [False, True]

    async def _fake_run(*_args, **_kwargs):
        ok = outcomes.pop(0)
        return ok, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    with pytest.raises(SystemExit):
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
                "1",
                "--workspace-allowlist",
                str(tmp_path),
            ]
        )

    run_dir = sorted(out_root.iterdir())[0]

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--config",
            str(cfg),
            "--out",
            str(out_root),
            "--profile",
            "minimal",
            "--resume-state",
            "--run-dir",
            str(run_dir),
            "--max-iterations",
            "3",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert [a["iteration"] for a in state["attempts"]] == [1, 2]
    assert state["current_iteration"] == 2
    assert state.get("resume_diagnostics") == []


def test_autonomous_resume_state_recovers_from_corrupt_state_artifact(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"
    run_dir = tmp_path / "run-corrupt"
    artifacts_dir = run_dir / ".autodev"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    (artifacts_dir / "autonomous_state.json").write_text("{not-valid-json", encoding="utf-8")
    (artifacts_dir / "autonomous_report.json").write_text(
        json.dumps(
            {
                "ok": False,
                "attempts": [
                    {
                        "iteration": 1,
                        "ok": False,
                        "resume": False,
                        "reason": "quality_gate_failed",
                        "strategy": {"name": "mixed", "rotation_applied": False},
                        "gate_results": {"passed": False, "fail_reasons": [{"code": "tests.min_pass_rate_not_met"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    async def _fake_run(*_args, **_kwargs):
        return True, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--config",
            str(cfg),
            "--out",
            str(out_root),
            "--profile",
            "minimal",
            "--resume-state",
            "--run-dir",
            str(run_dir),
            "--max-iterations",
            "3",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    state = json.loads((artifacts_dir / "autonomous_state.json").read_text(encoding="utf-8"))
    report = json.loads((artifacts_dir / "autonomous_report.json").read_text(encoding="utf-8"))

    assert [a["iteration"] for a in state["attempts"]] == [1, 2]
    assert state["current_iteration"] == 2
    codes = {item.get("code") for item in state.get("resume_diagnostics", []) if isinstance(item, dict)}
    assert "resume.state.invalid_json" in codes
    assert "resume.state.recovered_from_report_artifact" in codes
    assert report["resume_warning_count"] >= 2


def test_autonomous_resume_state_deduplicates_attempt_indices_before_retry(tmp_path, monkeypatch):
    cfg = _write_cfg(tmp_path)
    prd = _write_prd(tmp_path)
    out_root = tmp_path / "runs"
    run_dir = tmp_path / "run-duplicate"
    artifacts_dir = run_dir / ".autodev"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    state = autonomous_mode._new_state(
        run_id="run-dup",
        request_id="req-dup",
        run_out=str(run_dir),
        profile="minimal",
        policy=autonomous_mode.AutonomousPolicy(
            max_iterations=3,
            time_budget_sec=600,
            workspace_allowlist=[str(tmp_path)],
            blocked_paths=[],
            allow_docker_build=False,
            allow_external_side_effects=False,
        ),
        preflight_policy=autonomous_mode.AutonomousPreflightPolicy(),
        quality_gate_policy=None,
        stop_guard_policy=autonomous_mode.AutonomousStopGuardPolicy(),
        budget_guard_policy=autonomous_mode.AutonomousBudgetGuardPolicy(
            max_wall_clock_seconds=600,
            max_autonomous_iterations=3,
            max_estimated_token_budget=None,
        ),
        prd_path=str(prd),
        config_path=str(cfg),
    )
    state["current_iteration"] = 1
    state["attempts"] = [
        {
            "iteration": 1,
            "ok": False,
            "resume": False,
            "reason": "first",
            "strategy": {"name": "mixed", "rotation_applied": False},
        },
        {
            "iteration": 1,
            "ok": False,
            "resume": True,
            "reason": "duplicate",
            "strategy": {"name": "tests-focused", "rotation_applied": False},
        },
    ]
    (artifacts_dir / "autonomous_state.json").write_text(json.dumps(state), encoding="utf-8")

    monkeypatch.setattr(autonomous_mode, "LLMClient", _FakeClient)

    async def _fake_run(*_args, **_kwargs):
        return True, {"project": {}}, {"tasks": []}, []

    monkeypatch.setattr(autonomous_mode, "run_autodev_enterprise", _fake_run)

    autonomous_mode.cli(
        [
            "start",
            "--prd",
            str(prd),
            "--config",
            str(cfg),
            "--out",
            str(out_root),
            "--profile",
            "minimal",
            "--resume-state",
            "--run-dir",
            str(run_dir),
            "--max-iterations",
            "3",
            "--workspace-allowlist",
            str(tmp_path),
        ]
    )

    recovered_state = json.loads((artifacts_dir / "autonomous_state.json").read_text(encoding="utf-8"))
    iterations = [a["iteration"] for a in recovered_state["attempts"]]
    assert iterations == [1, 2]
    assert recovered_state["attempts"][0]["reason"] == "duplicate"
    codes = {item.get("code") for item in recovered_state.get("resume_diagnostics", []) if isinstance(item, dict)}
    assert "resume.state.attempts_deduplicated" in codes
