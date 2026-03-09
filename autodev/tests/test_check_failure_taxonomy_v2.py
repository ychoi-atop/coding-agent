from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent.parent


def _run_checker(*, schema: Path | None = None, example: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "scripts/check_failure_taxonomy_v2.py"]
    if schema is not None:
        cmd.extend(["--schema", str(schema)])
    if example is not None:
        cmd.extend(["--example", str(example)])
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_failure_taxonomy_v2_passes_for_repo_contract() -> None:
    proc = _run_checker()

    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "[PASS] failure taxonomy v2 schema/example validation passed" in proc.stdout


def test_check_failure_taxonomy_v2_fails_when_drill_lane_mismatches_class(tmp_path: Path) -> None:
    bad = tmp_path / "bad_failure_taxonomy_example.json"
    bad.write_text(
        """
{
  "policy_id": "autonomous.failure-taxonomy.v2",
  "version": "v1",
  "failure_classes": [
    {
      "id": "quality_gate_transient",
      "retryability": "retryable",
      "remediation_lane": "auto_fix",
      "code_families": ["tests.*"],
      "owner_team": "Feature Engineering",
      "rationale": "Transient failures are usually safe for bounded retries."
    },
    {
      "id": "quality_gate_deterministic",
      "retryability": "non_retryable",
      "remediation_lane": "manual",
      "code_families": ["security.*"],
      "owner_team": "Quality Engineering",
      "rationale": "Deterministic failures require manual root-cause remediation first."
    },
    {
      "id": "preflight_policy_violation",
      "retryability": "non_retryable",
      "remediation_lane": "manual",
      "code_families": ["autonomous_preflight.*"],
      "owner_team": "Platform Operations",
      "rationale": "Preflight blockers must be resolved before autonomous execution can continue."
    },
    {
      "id": "guard_control_stop",
      "retryability": "non_retryable",
      "remediation_lane": "escalate",
      "code_families": ["autonomous_guard.*"],
      "owner_team": "Release Engineering",
      "rationale": "Guard controls represent hard intervention points requiring escalation."
    },
    {
      "id": "tooling_runtime_flake",
      "retryability": "retryable",
      "remediation_lane": "auto_fix",
      "code_families": ["runtime.*"],
      "owner_team": "Developer Experience",
      "rationale": "Runtime flakes can usually be retried with bounded automation."
    },
    {
      "id": "budget_guard_stop",
      "retryability": "non_retryable",
      "remediation_lane": "escalate",
      "code_families": ["autonomous_budget_guard.*"],
      "owner_team": "Release Engineering",
      "rationale": "Budget guard violations need scope/budget approval before rerun."
    }
  ],
  "drill_examples": [
    {
      "id": "TX-01",
      "failure_class": "quality_gate_transient",
      "typed_code": "tests.min_pass_rate_not_met",
      "expected_lane": "manual"
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    proc = _run_checker(example=bad)

    assert proc.returncode == 1
    assert "lane mismatch" in proc.stdout
