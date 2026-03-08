from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from .autonomous_evidence_schema import AUTONOMOUS_EVIDENCE_SCHEMA_VERSION
from .autonomous_gate_signals import (
    NormalizedValidationSignal,
    make_gate_failure_reason,
    normalize_validation_signals,
)
from .cli_progress import make_cli_progress_callback
from .config import load_config
from .json_utils import json_dumps
from .llm_client import LLMClient, ModelEndpoint, ModelRouter
from .loop import run_autodev_enterprise
from .report import write_report
from .workspace import Workspace

logger = logging.getLogger("autodev")

AUTONOMOUS_STATE_FILE = ".autodev/autonomous_state.json"
AUTONOMOUS_REPORT_JSON = ".autodev/autonomous_report.json"
AUTONOMOUS_REPORT_MD = "AUTONOMOUS_REPORT.md"
AUTONOMOUS_GATE_RESULTS_JSON = ".autodev/autonomous_gate_results.json"
AUTONOMOUS_GATE_BASELINE_JSON = ".autodev/autonomous_gate_baseline.json"
AUTONOMOUS_STRATEGY_TRACE_JSON = ".autodev/autonomous_strategy_trace.json"
AUTONOMOUS_GUARD_DECISIONS_JSON = ".autodev/autonomous_guard_decisions.json"
_AUTONOMOUS_GATE_BASELINE_HISTORY_LIMIT = 20
_AUTONOMOUS_RESUME_DIAGNOSTIC_VERSION = "av2-008"
_AUTONOMOUS_PREFLIGHT_DIAGNOSTIC_VERSION = "av2-009"
_AUTONOMOUS_BUDGET_GUARD_DIAGNOSTIC_VERSION = "av2-010"
_AUTONOMOUS_OPERATOR_GUIDANCE_VERSION = "av2-011"
_AUTONOMOUS_INCIDENT_ROUTING_VERSION = "av3-004-v1"
_AUTONOMOUS_FAILURE_PLAYBOOK_DOC = "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md"

_AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_GATE_FAILURES = 3
_AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_NO_IMPROVEMENT = 2

_AUTONOMOUS_FIX_STRATEGY_ORDER = [
    "tests-focused",
    "security-focused",
    "perf-focused",
    "mixed",
]

_AUTONOMOUS_FIX_STRATEGY_HINTS = {
    "tests-focused": [
        "Prioritize failing test paths and assertions before broad refactors.",
        "Minimize scope to deterministic test fixes and dependency-safe changes.",
    ],
    "security-focused": [
        "Prioritize remediation of high-severity findings and unsafe patterns.",
        "Prefer explicit validation/sanitization with least-privilege defaults.",
    ],
    "perf-focused": [
        "Target hotspots tied to regression signals before unrelated cleanups.",
        "Prefer measurement-backed optimizations and avoid broad behavioral churn.",
    ],
    "mixed": [
        "Apply a balanced fix pass across tests, security, and performance signals.",
        "Start with highest-confidence blockers while preserving bounded scope.",
    ],
}


_OPERATOR_GUIDANCE_BY_CODE: dict[str, dict[str, Any]] = {
    "tests.min_pass_rate_not_met": {
        "family": "gate",
        "title": "Tests gate failed (pass-rate threshold not met)",
        "playbook_anchor": "#gate-failures",
        "actions": [
            "Inspect the latest pytest failures and isolate deterministic regressions first.",
            "Rerun targeted tests, then full suite, before resuming autonomous retries.",
        ],
    },
    "security.max_high_findings_exceeded": {
        "family": "gate",
        "title": "Security gate failed (high findings threshold exceeded)",
        "playbook_anchor": "#gate-failures",
        "actions": [
            "Review high-severity findings and patch/mitigate unsafe code paths.",
            "Re-run security checks and confirm the finding count is within policy.",
        ],
    },
    "performance.max_regression_pct_exceeded": {
        "family": "gate",
        "title": "Performance gate failed (regression threshold exceeded)",
        "playbook_anchor": "#gate-failures",
        "actions": [
            "Inspect recent hot-path changes and profile likely bottlenecks.",
            "Verify regression signal with repeatable perf checks before retrying.",
        ],
    },
    "performance.baseline_regression_detected": {
        "family": "gate",
        "title": "Performance baseline regression detected",
        "playbook_anchor": "#gate-failures",
        "actions": [
            "Compare latest perf sample against baseline artifact history to identify deltas.",
            "Apply focused optimization or rollback the regression-inducing change set.",
        ],
    },
    "autonomous_guard.repeated_gate_failure_limit_reached": {
        "family": "guard",
        "title": "Stop-guard halted repeated gate failures",
        "playbook_anchor": "#guard-stops",
        "actions": [
            "Pause autonomous retries and run a manual triage on recurring gate failures.",
            "Consider rollback or narrower scoped fix before resuming autonomous mode.",
        ],
    },
    "autonomous_guard.no_measurable_gate_improvement_limit_reached": {
        "family": "guard",
        "title": "Stop-guard halted no-improvement retry pattern",
        "playbook_anchor": "#guard-stops",
        "actions": [
            "Change fix strategy (scope/module/owner) instead of repeating similar retries.",
            "Resume only after measurable improvement criteria are defined.",
        ],
    },
    "autonomous_preflight.path_not_allowlisted": {
        "family": "preflight",
        "title": "Preflight failed: path not allowlisted",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Update workspace allowlist to include the intended PRD/config/output paths.",
            "Re-run preflight to verify path policy before starting unattended loop.",
        ],
    },
    "autonomous_preflight.path_blocked": {
        "family": "preflight",
        "title": "Preflight failed: path matched blocked list",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Move run inputs/outputs away from blocked paths or adjust blocked policy safely.",
            "Re-run preflight and confirm no blocked path matches remain.",
        ],
    },
    "autonomous_preflight.required_file_missing": {
        "family": "preflight",
        "title": "Preflight failed: required file missing",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Restore or point to required artifacts (PRD/config) before retry.",
            "Confirm file existence from the autonomous run context path.",
        ],
    },
    "autonomous_preflight.required_file_unreadable": {
        "family": "preflight",
        "title": "Preflight failed: required file unreadable",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Fix file permissions/ownership so autonomous runtime can read prerequisites.",
            "Re-run preflight after permission correction.",
        ],
    },
    "autonomous_preflight.artifacts_not_writable": {
        "family": "preflight",
        "title": "Preflight failed: artifact directory not writable",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Ensure `.autodev/` path is writable by the operator/runtime user.",
            "Re-run with `--preflight-check-artifact-writable` to verify fix.",
        ],
    },
    "autonomous_budget_guard.max_wall_clock_seconds_exceeded": {
        "family": "budget_guard",
        "title": "Budget-guard stop: wall-clock limit exceeded",
        "playbook_anchor": "#budget-guard-stops",
        "actions": [
            "Increase time budget only with explicit approval, or split scope into smaller runs.",
            "Resume from state only after tightening objective scope.",
        ],
    },
    "autonomous_budget_guard.max_autonomous_iterations_reached": {
        "family": "budget_guard",
        "title": "Budget-guard stop: max autonomous iterations reached",
        "playbook_anchor": "#budget-guard-stops",
        "actions": [
            "Review failed attempts and remove repeated ineffective retry patterns.",
            "Increase iteration cap only after strategy change or scope reduction.",
        ],
    },
    "autonomous_budget_guard.estimated_token_budget_not_available": {
        "family": "budget_guard",
        "title": "Budget-guard diagnostic: estimated token budget unavailable",
        "playbook_anchor": "#budget-guard-stops",
        "actions": [
            "Treat token budget as advisory until a reliable token signal is integrated.",
            "Use wall-clock and iteration guards as primary operational controls.",
        ],
    },
}

_OPERATOR_GUIDANCE_FAMILY_FALLBACK: dict[str, dict[str, Any]] = {
    "gate": {
        "title": "Quality gate failure requires operator triage",
        "playbook_anchor": "#gate-failures",
        "actions": [
            "Review gate diagnostics and fix the highest-confidence blocker before retrying.",
        ],
    },
    "guard": {
        "title": "Stop-guard halt requires manual intervention",
        "playbook_anchor": "#guard-stops",
        "actions": [
            "Pause unattended retries and decide whether to rollback, narrow scope, or change strategy.",
        ],
    },
    "preflight": {
        "title": "Preflight failure blocks unattended start",
        "playbook_anchor": "#preflight-failures",
        "actions": [
            "Resolve path/prerequisite issues and rerun preflight before autonomous start.",
        ],
    },
    "budget_guard": {
        "title": "Budget-guard threshold reached",
        "playbook_anchor": "#budget-guard-stops",
        "actions": [
            "Re-scope run and adjust budget policy deliberately before continuing.",
        ],
    },
    "unknown": {
        "title": "Unmapped autonomous failure code",
        "playbook_anchor": "#unknown-or-unmapped-codes",
        "actions": [
            "Capture the typed code and context from artifacts, then escalate for playbook-map update.",
        ],
    },
}

_INCIDENT_ROUTING_BY_CODE: dict[str, dict[str, str]] = {
    "tests.min_pass_rate_not_met": {
        "owner_team": "Feature Engineering",
        "severity": "high",
        "target_sla": "4h",
        "escalation_class": "engineering_hotfix",
    },
    "security.max_high_findings_exceeded": {
        "owner_team": "Security Engineering",
        "severity": "critical",
        "target_sla": "1h",
        "escalation_class": "security_incident",
    },
    "performance.max_regression_pct_exceeded": {
        "owner_team": "Performance Engineering",
        "severity": "high",
        "target_sla": "4h",
        "escalation_class": "performance_regression",
    },
    "performance.baseline_regression_detected": {
        "owner_team": "Performance Engineering",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "performance_regression",
    },
    "autonomous_guard.repeated_gate_failure_limit_reached": {
        "owner_team": "Release Engineering",
        "severity": "high",
        "target_sla": "2h",
        "escalation_class": "autonomy_control",
    },
    "autonomous_guard.no_measurable_gate_improvement_limit_reached": {
        "owner_team": "Release Engineering",
        "severity": "medium",
        "target_sla": "4h",
        "escalation_class": "autonomy_control",
    },
    "autonomous_preflight.path_not_allowlisted": {
        "owner_team": "Platform Operations",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "run_configuration",
    },
    "autonomous_preflight.path_blocked": {
        "owner_team": "Platform Operations",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "run_configuration",
    },
    "autonomous_preflight.required_file_missing": {
        "owner_team": "Platform Operations",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "run_configuration",
    },
    "autonomous_preflight.required_file_unreadable": {
        "owner_team": "Platform Operations",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "run_configuration",
    },
    "autonomous_preflight.artifacts_not_writable": {
        "owner_team": "Platform Operations",
        "severity": "high",
        "target_sla": "4h",
        "escalation_class": "run_configuration",
    },
    "autonomous_budget_guard.max_wall_clock_seconds_exceeded": {
        "owner_team": "Release Engineering",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "budget_control",
    },
    "autonomous_budget_guard.max_autonomous_iterations_reached": {
        "owner_team": "Release Engineering",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "budget_control",
    },
    "autonomous_budget_guard.estimated_token_budget_not_available": {
        "owner_team": "LLM Platform",
        "severity": "low",
        "target_sla": "24h",
        "escalation_class": "telemetry_gap",
    },
}

_INCIDENT_ROUTING_FAMILY_FALLBACK: dict[str, dict[str, str]] = {
    "gate": {
        "owner_team": "Feature Engineering",
        "severity": "high",
        "target_sla": "4h",
        "escalation_class": "engineering_hotfix",
    },
    "guard": {
        "owner_team": "Release Engineering",
        "severity": "high",
        "target_sla": "2h",
        "escalation_class": "autonomy_control",
    },
    "preflight": {
        "owner_team": "Platform Operations",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "run_configuration",
    },
    "budget_guard": {
        "owner_team": "Release Engineering",
        "severity": "medium",
        "target_sla": "8h",
        "escalation_class": "budget_control",
    },
    "unknown": {
        "owner_team": "Autonomy On-Call",
        "severity": "medium",
        "target_sla": "12h",
        "escalation_class": "manual_triage",
    },
}


@dataclass(frozen=True)
class AutonomousPolicy:
    max_iterations: int
    time_budget_sec: int
    workspace_allowlist: list[str]
    blocked_paths: list[str]
    allow_docker_build: bool
    allow_external_side_effects: bool


@dataclass(frozen=True)
class AutonomousPreflightPolicy:
    check_artifact_writable: bool = False


@dataclass(frozen=True)
class AutonomousTestsGateThresholds:
    min_pass_rate: float | None = None


@dataclass(frozen=True)
class AutonomousSecurityGateThresholds:
    max_high_findings: int | None = None


@dataclass(frozen=True)
class AutonomousPerformanceGateThresholds:
    max_regression_pct: float | None = None


@dataclass(frozen=True)
class AutonomousQualityGatePolicy:
    tests: AutonomousTestsGateThresholds | None = None
    security: AutonomousSecurityGateThresholds | None = None
    performance: AutonomousPerformanceGateThresholds | None = None


@dataclass(frozen=True)
class AutonomousStopGuardPolicy:
    max_consecutive_gate_failures: int = _AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_GATE_FAILURES
    max_consecutive_no_improvement: int = _AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_NO_IMPROVEMENT
    rollback_recommendation_enabled: bool = True


@dataclass(frozen=True)
class AutonomousBudgetGuardPolicy:
    max_wall_clock_seconds: int
    max_autonomous_iterations: int
    max_estimated_token_budget: int | None = None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _estimate_security_high_findings(row: NormalizedValidationSignal) -> int:
    diagnostics = row.diagnostics
    direct = _as_int(diagnostics.get("high_findings"))
    if direct is not None and direct >= 0:
        return direct

    sev_counts = diagnostics.get("severity_counts")
    if isinstance(sev_counts, dict):
        sev_high = _as_int(sev_counts.get("high"))
        if sev_high is not None and sev_high >= 0:
            return sev_high

    text = f"{row.stdout}\n{row.stderr}".lower()
    for pattern in [r"high\s*[:=]\s*(\d+)", r"high findings\s*[:=]\s*(\d+)"]:
        m = re.search(pattern, text)
        if m:
            parsed = _as_int(m.group(1))
            if parsed is not None and parsed >= 0:
                return parsed

    return 1 if row.status == "failed" else 0


def _extract_performance_regression_pct(ws: Workspace, validation_rows: list[NormalizedValidationSignal]) -> tuple[float | None, str]:
    if ws.exists(".autodev/perf_baseline.json"):
        try:
            raw = ws.read_text(".autodev/perf_baseline.json")
            payload = json.loads(raw)
            if isinstance(payload, dict):
                last_check = payload.get("last_check_result")
                if isinstance(last_check, dict):
                    verdicts = last_check.get("verdicts")
                    if isinstance(verdicts, list) and verdicts:
                        ratios: list[float] = []
                        for verdict in verdicts:
                            if not isinstance(verdict, dict):
                                continue
                            ratio = _as_float(verdict.get("ratio"))
                            if ratio is None:
                                continue
                            if ratio > 0:
                                ratios.append(ratio * 100.0)
                        if ratios:
                            return max(ratios), "perf_baseline.last_check_result.verdicts"
        except Exception:
            pass

    fallback: list[float] = []
    for row in validation_rows:
        diagnostics = row.diagnostics
        for key in ("regression_pct", "performance_regression_pct", "max_regression_pct"):
            value = _as_float(diagnostics.get(key))
            if value is not None and value >= 0:
                fallback.append(value)
    if fallback:
        return max(fallback), "final_validation.diagnostics"

    return None, "unavailable"


def _new_gate_baseline_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "history_limit": _AUTONOMOUS_GATE_BASELINE_HISTORY_LIMIT,
        "updated_at": _utc_now(),
        "gates": {},
    }


def _load_gate_baseline_payload(ws: Workspace) -> dict[str, Any]:
    if not ws.exists(AUTONOMOUS_GATE_BASELINE_JSON):
        return _new_gate_baseline_payload()
    try:
        raw = ws.read_text(AUTONOMOUS_GATE_BASELINE_JSON)
        payload = json.loads(raw)
    except Exception:
        return _new_gate_baseline_payload()
    if not isinstance(payload, dict):
        return _new_gate_baseline_payload()
    gates = payload.get("gates")
    if not isinstance(gates, dict):
        payload["gates"] = {}
    payload["version"] = 1
    payload["history_limit"] = int(payload.get("history_limit") or _AUTONOMOUS_GATE_BASELINE_HISTORY_LIMIT)
    return payload


def _write_gate_baseline_payload(ws: Workspace, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _utc_now()
    ws.write_text(AUTONOMOUS_GATE_BASELINE_JSON, json_dumps(payload))


def _baseline_recent_values(payload: dict[str, Any], gate: str) -> list[float]:
    gates = payload.get("gates")
    if not isinstance(gates, dict):
        return []
    gate_payload = gates.get(gate)
    if not isinstance(gate_payload, dict):
        return []
    observations = gate_payload.get("observations")
    if not isinstance(observations, list):
        return []

    values: list[float] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        parsed = _as_float(item.get("value"))
        if parsed is None:
            continue
        values.append(parsed)
    return values


def _append_gate_baseline_observation(
    payload: dict[str, Any],
    *,
    gate: str,
    metric: str,
    direction: str,
    value: float,
    signal_source: str,
) -> None:
    if value < 0:
        return

    gates = payload.setdefault("gates", {})
    if not isinstance(gates, dict):
        return

    gate_payload = gates.get(gate)
    if not isinstance(gate_payload, dict):
        gate_payload = {}
        gates[gate] = gate_payload

    gate_payload["metric"] = metric
    gate_payload["direction"] = direction

    observations = gate_payload.get("observations")
    if not isinstance(observations, list):
        observations = []
        gate_payload["observations"] = observations

    observations.append(
        {
            "value": value,
            "observed_at": _utc_now(),
            "signal_source": signal_source,
        }
    )

    history_limit = _as_int(payload.get("history_limit"))
    if history_limit is None or history_limit <= 0:
        history_limit = _AUTONOMOUS_GATE_BASELINE_HISTORY_LIMIT
        payload["history_limit"] = history_limit
    if len(observations) > history_limit:
        del observations[:-history_limit]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _detect_baseline_performance_regression(
    observed_regression_pct: float,
    baseline_values: list[float],
) -> tuple[bool, dict[str, Any]]:
    reference = _median(baseline_values)
    if reference is None:
        return False, {"sample_size": 0}

    sample_size = len(baseline_values)
    if sample_size < 2:
        return False, {
            "sample_size": sample_size,
            "baseline_reference_regression_pct": reference,
            "baseline_regression_limit_pct": None,
            "note": "insufficient_baseline_history",
        }

    tolerance = max(1.0, reference * 0.25)
    regression_limit = reference + tolerance
    delta = observed_regression_pct - reference
    regressed = observed_regression_pct > regression_limit
    return regressed, {
        "sample_size": sample_size,
        "baseline_reference_regression_pct": reference,
        "baseline_regression_limit_pct": regression_limit,
        "regression_delta_pct": delta,
    }


def _evaluate_quality_gates(
    *,
    ws: Workspace,
    policy: AutonomousQualityGatePolicy,
    last_validation: Any,
) -> dict[str, Any]:
    validation_rows = normalize_validation_signals(last_validation)
    gates: dict[str, Any] = {}
    fail_reasons: list[dict[str, Any]] = []
    baseline_payload = _load_gate_baseline_payload(ws)

    tests_cfg = policy.tests
    if tests_cfg is None or tests_cfg.min_pass_rate is None:
        gates["tests"] = {"status": "not_configured"}
    else:
        pytest_rows = [row for row in validation_rows if row.name == "pytest"]
        if not pytest_rows:
            gates["tests"] = {
                "status": "skipped",
                "threshold": {"min_pass_rate": tests_cfg.min_pass_rate},
                "observed": {"pass_rate": None, "sample_size": 0},
                "signal_source": "final_validation.pytest",
                "note": "pytest signal unavailable",
            }
        else:
            passed = sum(1 for row in pytest_rows if row.status == "passed")
            total = len(pytest_rows)
            pass_rate = passed / total
            gate_ok = pass_rate >= tests_cfg.min_pass_rate
            gates["tests"] = {
                "status": "passed" if gate_ok else "failed",
                "threshold": {"min_pass_rate": tests_cfg.min_pass_rate},
                "observed": {"pass_rate": pass_rate, "passed": passed, "sample_size": total},
                "signal_source": "final_validation.pytest",
            }
            _append_gate_baseline_observation(
                baseline_payload,
                gate="tests",
                metric="pass_rate",
                direction="higher_is_better",
                value=pass_rate,
                signal_source="final_validation.pytest",
            )
            if not gate_ok:
                fail_reasons.append(
                    make_gate_failure_reason(
                        gate="tests",
                        code="tests.min_pass_rate_not_met",
                        message="Pytest pass rate below configured threshold.",
                        signal_source="final_validation.pytest",
                        threshold={"min_pass_rate": tests_cfg.min_pass_rate},
                        observed={"pass_rate": pass_rate, "passed": passed, "sample_size": total},
                    )
                )

    security_cfg = policy.security
    if security_cfg is None or security_cfg.max_high_findings is None:
        gates["security"] = {"status": "not_configured"}
    else:
        security_validators = {"bandit", "semgrep", "pip_audit"}
        security_rows = [row for row in validation_rows if row.name in security_validators]
        if not security_rows:
            gates["security"] = {
                "status": "skipped",
                "threshold": {"max_high_findings": security_cfg.max_high_findings},
                "observed": {"high_findings": None, "sample_size": 0},
                "signal_source": "final_validation.security_validators",
                "note": "security signal unavailable",
            }
        else:
            high_findings = sum(_estimate_security_high_findings(row) for row in security_rows)
            gate_ok = high_findings <= security_cfg.max_high_findings
            gates["security"] = {
                "status": "passed" if gate_ok else "failed",
                "threshold": {"max_high_findings": security_cfg.max_high_findings},
                "observed": {"high_findings": high_findings, "sample_size": len(security_rows)},
                "signal_source": "final_validation.security_validators",
            }
            _append_gate_baseline_observation(
                baseline_payload,
                gate="security",
                metric="high_findings",
                direction="lower_is_better",
                value=float(high_findings),
                signal_source="final_validation.security_validators",
            )
            if not gate_ok:
                fail_reasons.append(
                    make_gate_failure_reason(
                        gate="security",
                        code="security.max_high_findings_exceeded",
                        message="Estimated high severity findings exceeded configured threshold.",
                        signal_source="final_validation.security_validators",
                        threshold={"max_high_findings": security_cfg.max_high_findings},
                        observed={"high_findings": high_findings, "sample_size": len(security_rows)},
                    )
                )

    performance_cfg = policy.performance
    if performance_cfg is None or performance_cfg.max_regression_pct is None:
        gates["performance"] = {"status": "not_configured"}
    else:
        observed_regression_pct, signal_source = _extract_performance_regression_pct(ws, validation_rows)
        if observed_regression_pct is None:
            gates["performance"] = {
                "status": "skipped",
                "threshold": {"max_regression_pct": performance_cfg.max_regression_pct},
                "observed": {"regression_pct": None},
                "signal_source": signal_source,
                "note": "performance regression signal unavailable",
            }
        else:
            baseline_values = _baseline_recent_values(baseline_payload, "performance")
            baseline_regressed, baseline_details = _detect_baseline_performance_regression(
                observed_regression_pct,
                baseline_values,
            )
            threshold_gate_ok = observed_regression_pct <= performance_cfg.max_regression_pct
            gate_ok = threshold_gate_ok and not baseline_regressed
            gates["performance"] = {
                "status": "passed" if gate_ok else "failed",
                "threshold": {"max_regression_pct": performance_cfg.max_regression_pct},
                "observed": {"regression_pct": observed_regression_pct},
                "signal_source": signal_source,
                "baseline": baseline_details,
            }
            _append_gate_baseline_observation(
                baseline_payload,
                gate="performance",
                metric="regression_pct",
                direction="lower_is_better",
                value=observed_regression_pct,
                signal_source=signal_source,
            )
            if not threshold_gate_ok:
                fail_reasons.append(
                    make_gate_failure_reason(
                        gate="performance",
                        code="performance.max_regression_pct_exceeded",
                        message="Performance regression exceeded configured threshold.",
                        signal_source=signal_source,
                        threshold={"max_regression_pct": performance_cfg.max_regression_pct},
                        observed={"regression_pct": observed_regression_pct},
                    )
                )
            if baseline_regressed:
                fail_reasons.append(
                    make_gate_failure_reason(
                        gate="performance",
                        code="performance.baseline_regression_detected",
                        message="Performance regression significantly exceeded recent baseline trend.",
                        signal_source=signal_source,
                        threshold={
                            "baseline_reference_regression_pct": baseline_details.get("baseline_reference_regression_pct"),
                            "baseline_regression_limit_pct": baseline_details.get("baseline_regression_limit_pct"),
                        },
                        observed={
                            "regression_pct": observed_regression_pct,
                            "baseline_sample_size": baseline_details.get("sample_size", 0),
                            "regression_delta_pct": baseline_details.get("regression_delta_pct"),
                        },
                    )
                )

    _write_gate_baseline_payload(ws, baseline_payload)

    passed = len(fail_reasons) == 0
    return {
        "evaluated_at": _utc_now(),
        "passed": passed,
        "gates": gates,
        "fail_reasons": fail_reasons,
    }


def _route_strategy_from_fail_reasons(fail_reasons: Any) -> dict[str, Any]:
    reasons = [r for r in fail_reasons if isinstance(r, dict)] if isinstance(fail_reasons, list) else []

    mapped: list[str] = []
    codes: list[str] = []
    categories: list[str] = []
    gates: list[str] = []
    for reason in reasons:
        code = str(reason.get("code") or "").strip().lower()
        category = str(reason.get("category") or "").strip().lower()
        gate = str(reason.get("gate") or "").strip().lower()
        if code:
            codes.append(code)
        if category:
            categories.append(category)
        if gate:
            gates.append(gate)

        if code.startswith("tests."):
            mapped.append("tests-focused")
        elif code.startswith("security."):
            mapped.append("security-focused")
        elif code.startswith("performance."):
            mapped.append("perf-focused")
        elif category == "reliability" or gate == "tests":
            mapped.append("tests-focused")
        elif category == "security" or gate == "security":
            mapped.append("security-focused")
        elif category == "performance" or gate == "performance":
            mapped.append("perf-focused")
        else:
            mapped.append("mixed")

    distinct = sorted(set(mapped))
    if not distinct:
        recommended = "mixed"
        rationale = "No typed gate fail reasons; default to mixed strategy."
    elif len(distinct) == 1:
        recommended = distinct[0]
        rationale = "Single gate failure domain mapped to focused strategy."
    else:
        recommended = "mixed"
        rationale = "Multiple gate failure domains detected; selecting mixed strategy."

    candidates = [recommended]
    if recommended != "mixed":
        candidates.append("mixed")
    for name in _AUTONOMOUS_FIX_STRATEGY_ORDER:
        if name not in candidates:
            candidates.append(name)

    return {
        "recommended": recommended,
        "rationale": rationale,
        "candidates": candidates,
        "gate_fail_codes": sorted(set(codes)),
        "gate_categories": sorted(set(categories)),
        "gate_names": sorted(set(gates)),
    }


def _gate_failure_summary(gate_results: Any) -> dict[str, Any]:
    if not isinstance(gate_results, dict):
        return {
            "available": False,
            "passed": False,
            "failed_gate_count": 0,
            "fail_reason_count": 0,
            "fail_codes": [],
        }

    gates = gate_results.get("gates") if isinstance(gate_results.get("gates"), dict) else {}
    failed_gate_count = len([
        name
        for name, row in gates.items()
        if isinstance(name, str)
        and isinstance(row, dict)
        and row.get("status") == "failed"
    ])

    fail_reasons = gate_results.get("fail_reasons") if isinstance(gate_results.get("fail_reasons"), list) else []
    fail_codes = sorted(
        set(
            str(reason.get("code"))
            for reason in fail_reasons
            if isinstance(reason, dict) and reason.get("code")
        )
    )

    return {
        "available": True,
        "passed": bool(gate_results.get("passed")),
        "failed_gate_count": failed_gate_count,
        "fail_reason_count": len(fail_reasons),
        "fail_codes": fail_codes,
    }


def _has_measurable_gate_improvement(previous: Any, current: Any) -> bool:
    prev = _gate_failure_summary(previous)
    cur = _gate_failure_summary(current)
    if not prev["available"] or not cur["available"]:
        return False
    if cur["passed"] and not prev["passed"]:
        return True
    if cur["failed_gate_count"] < prev["failed_gate_count"]:
        return True
    if cur["fail_reason_count"] < prev["fail_reason_count"]:
        return True
    if len(cur["fail_codes"]) < len(prev["fail_codes"]):
        return True
    return False


def _rotate_strategy_name(recommended: str, attempts: list[dict[str, Any]]) -> str:
    order = _AUTONOMOUS_FIX_STRATEGY_ORDER
    if recommended not in order:
        return "mixed"

    recent = [
        str((a.get("strategy") or {}).get("name"))
        for a in attempts[-2:]
        if isinstance(a, dict) and isinstance(a.get("strategy"), dict)
    ]
    start = order.index(recommended)
    for i in range(1, len(order) + 1):
        candidate = order[(start + i) % len(order)]
        if candidate not in recent:
            return candidate
    return order[(start + 1) % len(order)]


def _resolve_retry_strategy(attempts: list[dict[str, Any]], iteration: int) -> dict[str, Any]:
    if iteration <= 1 or not attempts:
        name = "mixed"
        return {
            "name": name,
            "hints": list(_AUTONOMOUS_FIX_STRATEGY_HINTS[name]),
            "recommended": name,
            "recommended_hints": list(_AUTONOMOUS_FIX_STRATEGY_HINTS[name]),
            "rationale": "Initial autonomous iteration uses balanced mixed strategy.",
            "selected_by": "initial_default",
            "rotation_applied": False,
            "rotation_reason": None,
            "gate_fail_codes": [],
            "gate_categories": [],
            "gate_names": [],
            "attempted_before": False,
        }

    latest = attempts[-1] if isinstance(attempts[-1], dict) else {}
    fail_reasons = latest.get("quality_gate_fail_reasons")
    if not isinstance(fail_reasons, list):
        gate_results = latest.get("gate_results")
        fail_reasons = gate_results.get("fail_reasons") if isinstance(gate_results, dict) else []

    route = _route_strategy_from_fail_reasons(fail_reasons)
    recommended = str(route.get("recommended") or "mixed")
    selected = recommended
    rotation_applied = False
    rotation_reason = None

    same_strategy_attempts = [
        a
        for a in attempts
        if isinstance(a, dict)
        and isinstance(a.get("strategy"), dict)
        and str(a["strategy"].get("name")) == recommended
        and isinstance(a.get("gate_results"), dict)
    ]
    if len(same_strategy_attempts) >= 2:
        previous_same = same_strategy_attempts[-2]
        latest_same = same_strategy_attempts[-1]
        if not _has_measurable_gate_improvement(previous_same.get("gate_results"), latest_same.get("gate_results")):
            selected = _rotate_strategy_name(recommended, attempts)
            rotation_applied = selected != recommended
            if rotation_applied:
                rotation_reason = "prior_same_strategy_no_measurable_gate_improvement"

    hints = list(_AUTONOMOUS_FIX_STRATEGY_HINTS.get(selected, _AUTONOMOUS_FIX_STRATEGY_HINTS["mixed"]))
    recommended_hints = list(_AUTONOMOUS_FIX_STRATEGY_HINTS.get(recommended, _AUTONOMOUS_FIX_STRATEGY_HINTS["mixed"]))

    if rotation_applied:
        rationale = (
            f"{route['rationale']} Rotated from {recommended} to {selected} "
            "after repeated no-improvement outcome under the same strategy."
        )
    else:
        rationale = str(route.get("rationale") or "")

    return {
        "name": selected,
        "hints": hints,
        "recommended": recommended,
        "recommended_hints": recommended_hints,
        "rationale": rationale,
        "selected_by": "gate_fail_routing",
        "rotation_applied": rotation_applied,
        "rotation_reason": rotation_reason,
        "gate_fail_codes": list(route.get("gate_fail_codes") or []),
        "gate_categories": list(route.get("gate_categories") or []),
        "gate_names": list(route.get("gate_names") or []),
        "attempted_before": len(same_strategy_attempts) > 0,
    }


def _write_strategy_trace_artifact(ws: Workspace, attempts: list[dict[str, Any]]) -> None:
    strategy_attempts: list[dict[str, Any]] = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        strategy = item.get("strategy") if isinstance(item.get("strategy"), dict) else None
        if strategy is None:
            continue
        strategy_attempts.append(
            {
                "iteration": item.get("iteration"),
                "ok": bool(item.get("ok")),
                "reason": item.get("reason"),
                "strategy": strategy,
                "gate_results_summary": _gate_failure_summary(item.get("gate_results")),
            }
        )

    latest = strategy_attempts[-1]["strategy"] if strategy_attempts else None
    payload = {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "attempts": strategy_attempts,
        "latest": latest,
    }
    _write_json_if_changed(ws, AUTONOMOUS_STRATEGY_TRACE_JSON, payload, ignore_keys=("updated_at",))


def _evaluate_stop_guard_decision(
    attempts: list[dict[str, Any]],
    policy: AutonomousStopGuardPolicy,
) -> dict[str, Any] | None:
    gate_attempts = [
        item
        for item in attempts
        if isinstance(item, dict) and isinstance(item.get("gate_results"), dict)
    ]
    if not gate_attempts:
        return None

    latest = gate_attempts[-1]
    latest_gate_results = latest.get("gate_results")
    if not isinstance(latest_gate_results, dict):
        return None
    if latest_gate_results.get("passed") is True:
        return None

    consecutive_failed_gate_attempts: list[dict[str, Any]] = []
    for item in reversed(gate_attempts):
        gate_results = item.get("gate_results")
        if not isinstance(gate_results, dict):
            break
        if gate_results.get("passed") is True:
            break
        consecutive_failed_gate_attempts.append(item)
    consecutive_failed_gate_attempts.reverse()

    if len(consecutive_failed_gate_attempts) >= policy.max_consecutive_gate_failures:
        return {
            "type": "autonomous_stop_guard",
            "taxonomy_version": "av2-007",
            "decision": "stop",
            "reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            "reason": "stop guard triggered by repeated consecutive gate failures",
            "triggered_at": _utc_now(),
            "iteration": latest.get("iteration"),
            "consecutive_failed_gate_attempts": len(consecutive_failed_gate_attempts),
            "threshold": policy.max_consecutive_gate_failures,
            "rollback_recommended": bool(policy.rollback_recommendation_enabled),
            "rollback_recommendation_marker": (
                "recommended" if policy.rollback_recommendation_enabled else "disabled"
            ),
        }

    no_improvement_streak = 0
    for idx in range(1, len(consecutive_failed_gate_attempts)):
        previous = consecutive_failed_gate_attempts[idx - 1]
        current = consecutive_failed_gate_attempts[idx]
        if not _has_measurable_gate_improvement(previous.get("gate_results"), current.get("gate_results")):
            no_improvement_streak += 1
        else:
            no_improvement_streak = 0

    if no_improvement_streak >= policy.max_consecutive_no_improvement:
        return {
            "type": "autonomous_stop_guard",
            "taxonomy_version": "av2-007",
            "decision": "stop",
            "reason_code": "autonomous_guard.no_measurable_gate_improvement_limit_reached",
            "reason": "stop guard triggered by repeated no-improvement gate outcomes",
            "triggered_at": _utc_now(),
            "iteration": latest.get("iteration"),
            "consecutive_no_improvement": no_improvement_streak,
            "threshold": policy.max_consecutive_no_improvement,
            "consecutive_failed_gate_attempts": len(consecutive_failed_gate_attempts),
            "rollback_recommended": bool(policy.rollback_recommendation_enabled),
            "rollback_recommendation_marker": (
                "recommended" if policy.rollback_recommendation_enabled else "disabled"
            ),
        }

    return None


def _write_guard_decisions_artifact(
    ws: Workspace,
    *,
    policy: AutonomousStopGuardPolicy,
    attempts: list[dict[str, Any]],
) -> None:
    decisions: list[dict[str, Any]] = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        guard_decision = item.get("guard_decision")
        if isinstance(guard_decision, dict):
            decisions.append(
                {
                    "iteration": item.get("iteration"),
                    "ok": bool(item.get("ok")),
                    "reason": item.get("reason"),
                    "guard_decision": guard_decision,
                }
            )

    payload = {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "policy": asdict(policy),
        "decisions": decisions,
        "latest": decisions[-1]["guard_decision"] if decisions else None,
    }
    _write_json_if_changed(ws, AUTONOMOUS_GUARD_DECISIONS_JSON, payload, ignore_keys=("updated_at",))


def _latest_strategy_from_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(attempts):
        if isinstance(item, dict) and isinstance(item.get("strategy"), dict):
            return item["strategy"]
    return None


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "ts": _utc_now(),
        "event": event,
        **fields,
    }
    if logger.handlers:
        logger.info(json_dumps(payload))
    else:
        print(json_dumps(payload))


def _slugify_prd_stem(prd_path: str) -> str:
    stem = Path(prd_path).stem.strip()
    if not stem:
        return "prd"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return slug or "prd"


def _resolve_output_dir(prd_path: str, out_root: str) -> str:
    prd_slug = _slugify_prd_stem(prd_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(out_root).expanduser()
    candidate = root / f"{prd_slug}_{ts}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{prd_slug}_{ts}_{suffix:02d}"
        suffix += 1
    return str(candidate)


def _coerce_optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, tuple):
        return [str(x) for x in value]
    return [str(value)]


def _coerce_int(value: Any, key: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be an integer, got {type(value).__name__}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be an integer, got {value!r}.")


def _coerce_optional_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be an integer, got {type(value).__name__}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be an integer, got {value!r}.")


def _coerce_max_parallel_tasks(value: Any, *, default: int = 2) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(
            "config.run.max_parallel_tasks must be an integer between 1 and 3 (recommended), "
            f"got {type(value).__name__}."
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.max_parallel_tasks must be an integer, got {value!r}.")
    if parsed < 1:
        raise SystemExit("config.run.max_parallel_tasks must be >= 1.")
    return parsed


def _coerce_optional_float(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be a number, got {type(value).__name__}.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be a number, got {value!r}.")


def _resolve_autonomous_quality_gate_policy(run_cfg: dict[str, Any]) -> AutonomousQualityGatePolicy | None:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is None:
        return None
    if not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")

    raw_policy = auto_cfg.get("quality_gate_policy")
    if raw_policy is None:
        return None
    if not isinstance(raw_policy, dict):
        raise SystemExit("config.run.autonomous.quality_gate_policy must be an object when set.")

    tests_thresholds = None
    raw_tests = raw_policy.get("tests")
    if raw_tests is not None:
        if not isinstance(raw_tests, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.tests must be an object.")
        min_pass_rate = _coerce_optional_float(
            raw_tests.get("min_pass_rate"),
            "autonomous.quality_gate_policy.tests.min_pass_rate",
        )
        if min_pass_rate is not None and (min_pass_rate < 0 or min_pass_rate > 1):
            raise SystemExit("config.run.autonomous.quality_gate_policy.tests.min_pass_rate must be between 0 and 1.")
        tests_thresholds = AutonomousTestsGateThresholds(min_pass_rate=min_pass_rate)

    security_thresholds = None
    raw_security = raw_policy.get("security")
    if raw_security is not None:
        if not isinstance(raw_security, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.security must be an object.")
        max_high_findings = _coerce_optional_int(
            raw_security.get("max_high_findings"),
            "autonomous.quality_gate_policy.security.max_high_findings",
        )
        if max_high_findings is not None and max_high_findings < 0:
            raise SystemExit("config.run.autonomous.quality_gate_policy.security.max_high_findings must be >= 0.")
        security_thresholds = AutonomousSecurityGateThresholds(max_high_findings=max_high_findings)

    performance_thresholds = None
    raw_performance = raw_policy.get("performance")
    if raw_performance is not None:
        if not isinstance(raw_performance, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.performance must be an object.")
        max_regression_pct = _coerce_optional_float(
            raw_performance.get("max_regression_pct"),
            "autonomous.quality_gate_policy.performance.max_regression_pct",
        )
        if max_regression_pct is not None and max_regression_pct < 0:
            raise SystemExit("config.run.autonomous.quality_gate_policy.performance.max_regression_pct must be >= 0.")
        performance_thresholds = AutonomousPerformanceGateThresholds(max_regression_pct=max_regression_pct)

    return AutonomousQualityGatePolicy(
        tests=tests_thresholds,
        security=security_thresholds,
        performance=performance_thresholds,
    )


def _resolve_autonomous_stop_guard_policy(run_cfg: dict[str, Any]) -> AutonomousStopGuardPolicy:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is None:
        return AutonomousStopGuardPolicy()
    if not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")

    raw_policy = auto_cfg.get("stop_guard_policy")
    if raw_policy is None:
        return AutonomousStopGuardPolicy()
    if not isinstance(raw_policy, dict):
        raise SystemExit("config.run.autonomous.stop_guard_policy must be an object when set.")

    repeated = _coerce_optional_int(
        raw_policy.get("max_consecutive_gate_failures"),
        "autonomous.stop_guard_policy.max_consecutive_gate_failures",
    )
    if repeated is None:
        repeated = _AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_GATE_FAILURES
    if repeated <= 0:
        raise SystemExit("config.run.autonomous.stop_guard_policy.max_consecutive_gate_failures must be >= 1.")

    no_improvement = _coerce_optional_int(
        raw_policy.get("max_consecutive_no_improvement"),
        "autonomous.stop_guard_policy.max_consecutive_no_improvement",
    )
    if no_improvement is None:
        no_improvement = _AUTONOMOUS_STOP_GUARD_DEFAULT_MAX_CONSECUTIVE_NO_IMPROVEMENT
    if no_improvement <= 0:
        raise SystemExit("config.run.autonomous.stop_guard_policy.max_consecutive_no_improvement must be >= 1.")

    rollback_enabled_raw = raw_policy.get("rollback_recommendation_enabled", True)
    if not isinstance(rollback_enabled_raw, bool):
        raise SystemExit("config.run.autonomous.stop_guard_policy.rollback_recommendation_enabled must be a boolean.")

    return AutonomousStopGuardPolicy(
        max_consecutive_gate_failures=int(repeated),
        max_consecutive_no_improvement=int(no_improvement),
        rollback_recommendation_enabled=rollback_enabled_raw,
    )


def _resolve_autonomous_budget_guard_policy(
    args: argparse.Namespace,
    run_cfg: dict[str, Any],
    policy: AutonomousPolicy,
) -> AutonomousBudgetGuardPolicy:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is not None and not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")
    auto_cfg = auto_cfg or {}

    raw_policy = auto_cfg.get("budget_guard_policy")
    if raw_policy is None:
        raw_policy = {}
    if not isinstance(raw_policy, dict):
        raise SystemExit("config.run.autonomous.budget_guard_policy must be an object when set.")

    token_budget_raw = args.max_estimated_token_budget
    if token_budget_raw is None:
        token_budget_raw = raw_policy.get("max_estimated_token_budget")
    max_estimated_token_budget = _coerce_optional_int(
        token_budget_raw,
        "autonomous.budget_guard_policy.max_estimated_token_budget",
    )
    if max_estimated_token_budget is not None and max_estimated_token_budget <= 0:
        raise SystemExit("config.run.autonomous.budget_guard_policy.max_estimated_token_budget must be >= 1.")

    return AutonomousBudgetGuardPolicy(
        max_wall_clock_seconds=policy.time_budget_sec,
        max_autonomous_iterations=policy.max_iterations,
        max_estimated_token_budget=max_estimated_token_budget,
    )


def _coerce_role_temperatures(value: Any) -> Dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit("llm.role_temperatures must be an object map of role->temperature.")

    out: Dict[str, float] = {}
    for role_name, raw_temp in value.items():
        if isinstance(raw_temp, bool):
            raise SystemExit(f"llm.role_temperatures.{role_name} must be a number.")
        if isinstance(raw_temp, str):
            raw_temp = raw_temp.strip()
        try:
            temp = float(raw_temp)
        except (TypeError, ValueError) as e:
            raise SystemExit(f"llm.role_temperatures.{role_name} must be a number.") from e
        if temp < 0 or temp > 2:
            raise SystemExit(f"llm.role_temperatures.{role_name} must be between 0 and 2.")
        out[str(role_name)] = temp
    return out


def _resolve_profile_name(requested: str | None, profiles: dict[str, Any]) -> str:
    if requested:
        if requested not in profiles:
            available = ", ".join(sorted(profiles))
            raise SystemExit(f"Profile '{requested}' not found. Available profiles: {available}")
        return requested

    if len(profiles) == 1:
        return next(iter(profiles.keys()))

    available = ", ".join(sorted(profiles))
    raise SystemExit(
        "Profile was not provided and more than one profile is configured. "
        f"Available profiles: {available}."
    )


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _make_preflight_diagnostic(
    code: str,
    message: str,
    *,
    reason_code: str,
    severity: str = "error",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "autonomous_preflight_failure",
        "taxonomy_version": _AUTONOMOUS_PREFLIGHT_DIAGNOSTIC_VERSION,
        "code": code,
        "reason_code": reason_code,
        "message": message,
        "severity": severity,
        "retryable": retryable,
        "at": _utc_now(),
    }
    if isinstance(details, dict) and details:
        payload["details"] = details
    return payload


def _resolve_autonomous_preflight_policy(args: argparse.Namespace, run_cfg: dict[str, Any]) -> AutonomousPreflightPolicy:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is not None and not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")
    auto_cfg = auto_cfg or {}

    preflight_cfg = auto_cfg.get("preflight")
    if preflight_cfg is not None and not isinstance(preflight_cfg, dict):
        raise SystemExit("config.run.autonomous.preflight must be an object when set.")
    preflight_cfg = preflight_cfg or {}

    check_artifact_writable = (
        bool(args.preflight_check_artifact_writable)
        if args.preflight_check_artifact_writable is not None
        else bool(preflight_cfg.get("check_artifact_writable", False))
    )
    return AutonomousPreflightPolicy(check_artifact_writable=check_artifact_writable)


def _run_autonomous_preflight(
    *,
    ws: Workspace,
    policy: AutonomousPolicy,
    preflight_policy: AutonomousPreflightPolicy,
    prd: str,
    config: str,
    out_root: str,
    run_out: str,
) -> dict[str, Any]:
    allowlist = [_normalize_path(x) for x in policy.workspace_allowlist]
    blocked = [_normalize_path(x) for x in policy.blocked_paths]

    targets = {
        "prd": _normalize_path(prd),
        "config": _normalize_path(config),
        "out_root": _normalize_path(out_root),
        "run_out": _normalize_path(run_out),
    }

    diagnostics: list[dict[str, Any]] = []

    for label, target in targets.items():
        if not any(_is_under(target, root) for root in allowlist):
            diagnostics.append(
                _make_preflight_diagnostic(
                    "preflight.path.not_in_workspace_allowlist",
                    f"path '{label}' is outside workspace_allowlist",
                    reason_code="autonomous_preflight.path_not_allowlisted",
                    details={"label": label, "path": target, "workspace_allowlist": allowlist},
                )
            )
        if any(_is_under(target, blocked_root) for blocked_root in blocked):
            diagnostics.append(
                _make_preflight_diagnostic(
                    "preflight.path.matches_blocked_path",
                    f"path '{label}' matches a blocked path",
                    reason_code="autonomous_preflight.path_blocked",
                    details={"label": label, "path": target, "blocked_paths": blocked},
                )
            )

    def _check_readable_file(path: str, label: str) -> None:
        if not os.path.isfile(path):
            diagnostics.append(
                _make_preflight_diagnostic(
                    "preflight.prerequisite.missing_file",
                    f"required file '{label}' is missing",
                    reason_code="autonomous_preflight.required_file_missing",
                    retryable=False,
                    details={"label": label, "path": path},
                )
            )
            return
        if not os.access(path, os.R_OK):
            diagnostics.append(
                _make_preflight_diagnostic(
                    "preflight.prerequisite.unreadable_file",
                    f"required file '{label}' is not readable",
                    reason_code="autonomous_preflight.required_file_unreadable",
                    retryable=False,
                    details={"label": label, "path": path},
                )
            )

    _check_readable_file(targets["prd"], "prd")
    _check_readable_file(targets["config"], "config")

    if preflight_policy.check_artifact_writable:
        probe_rel_path = ".autodev/.preflight_write_probe"
        probe_payload = f"autonomous-preflight {uuid4().hex}\n"
        try:
            ws.write_text(probe_rel_path, probe_payload)
            probe_abs = Path(ws.root) / probe_rel_path
            if probe_abs.exists():
                probe_abs.unlink()
        except OSError as e:
            diagnostics.append(
                _make_preflight_diagnostic(
                    "preflight.artifacts.not_writable",
                    "artifact directory is not writable",
                    reason_code="autonomous_preflight.artifacts_not_writable",
                    retryable=True,
                    details={"run_out": targets["run_out"], "error": str(e)},
                )
            )

    reason_codes = [
        str(item.get("reason_code"))
        for item in diagnostics
        if isinstance(item, dict) and item.get("reason_code")
    ]

    return {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "status": "passed" if not diagnostics else "failed",
        "ok": len(diagnostics) == 0,
        "taxonomy_version": _AUTONOMOUS_PREFLIGHT_DIAGNOSTIC_VERSION,
        "reason_codes": sorted(set(reason_codes)),
        "diagnostics": diagnostics,
        "artifact_writability_check_enabled": preflight_policy.check_artifact_writable,
        "checked_at": _utc_now(),
    }


def _read_text_file(path: str, label: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        raise SystemExit(f"{label} not found: {path}") from e
    except OSError as e:
        raise SystemExit(f"Unable to read {label}: {path} ({e})") from e


def _resolve_autonomous_policy(args: argparse.Namespace, run_cfg: dict[str, Any]) -> AutonomousPolicy:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is not None and not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")
    auto_cfg = auto_cfg or {}

    max_iterations_cfg = auto_cfg.get("max_iterations", 3)
    max_iterations = int(args.max_iterations if args.max_iterations is not None else max_iterations_cfg)
    if max_iterations <= 0:
        raise SystemExit("autonomous max_iterations must be >= 1")

    time_budget_cfg = auto_cfg.get("time_budget_sec", 3600)
    time_budget_sec = int(args.time_budget_sec if args.time_budget_sec is not None else time_budget_cfg)
    if time_budget_sec <= 0:
        raise SystemExit("autonomous time_budget_sec must be >= 1")

    default_allowlist = [str(Path.cwd().resolve())]
    allowlist = args.workspace_allowlist or auto_cfg.get("workspace_allowlist") or default_allowlist
    if not isinstance(allowlist, list) or not allowlist:
        raise SystemExit("autonomous workspace_allowlist must be a non-empty list")

    blocked = args.blocked_paths
    if blocked is None:
        blocked = auto_cfg.get("blocked_paths")
    blocked = blocked or []
    if not isinstance(blocked, list):
        raise SystemExit("autonomous blocked_paths must be a list")

    side_effect_cfg = auto_cfg.get("external_side_effects")
    if side_effect_cfg is not None and not isinstance(side_effect_cfg, dict):
        raise SystemExit("config.run.autonomous.external_side_effects must be an object")
    side_effect_cfg = side_effect_cfg or {}

    allow_docker_build = bool(
        args.allow_docker_build
        if args.allow_docker_build is not None
        else side_effect_cfg.get("allow_docker_build", False)
    )
    allow_external_side_effects = bool(
        args.allow_external_side_effects
        if args.allow_external_side_effects is not None
        else side_effect_cfg.get("allow_external_side_effects", False)
    )

    return AutonomousPolicy(
        max_iterations=max_iterations,
        time_budget_sec=time_budget_sec,
        workspace_allowlist=[str(x) for x in allowlist],
        blocked_paths=[str(x) for x in blocked],
        allow_docker_build=allow_docker_build,
        allow_external_side_effects=allow_external_side_effects,
    )


def _new_state(
    *,
    run_id: str,
    request_id: str,
    run_out: str,
    profile: str,
    policy: AutonomousPolicy,
    preflight_policy: AutonomousPreflightPolicy,
    quality_gate_policy: AutonomousQualityGatePolicy | None,
    stop_guard_policy: AutonomousStopGuardPolicy,
    budget_guard_policy: AutonomousBudgetGuardPolicy,
    prd_path: str,
    config_path: str,
) -> dict[str, Any]:
    policy_payload: dict[str, Any] = {
        "max_iterations": policy.max_iterations,
        "time_budget_sec": policy.time_budget_sec,
        "workspace_allowlist": [_normalize_path(x) for x in policy.workspace_allowlist],
        "blocked_paths": [_normalize_path(x) for x in policy.blocked_paths],
        "allow_docker_build": policy.allow_docker_build,
        "allow_external_side_effects": policy.allow_external_side_effects,
    }
    policy_payload["preflight_policy"] = asdict(preflight_policy)
    if quality_gate_policy is not None:
        policy_payload["quality_gate_policy"] = asdict(quality_gate_policy)
    policy_payload["stop_guard_policy"] = asdict(stop_guard_policy)
    policy_payload["budget_guard_policy"] = asdict(budget_guard_policy)

    return {
        "version": 1,
        "mode": "autonomous_v1",
        "status": "running",
        "phase": "ingest",
        "run_id": run_id,
        "request_id": request_id,
        "profile": profile,
        "run_out": os.path.abspath(run_out),
        "prd": os.path.abspath(prd_path),
        "config": os.path.abspath(config_path),
        "policy": policy_payload,
        "current_iteration": 0,
        "attempts": [],
        "resume_diagnostics": [],
        "last_strategy": None,
        "preflight": {"status": "pending", "ok": None, "reason_codes": [], "diagnostics": []},
        "budget_guard": _make_budget_guard_snapshot(
            policy=budget_guard_policy,
            elapsed_seconds=0,
            current_iteration=0,
        ),
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _make_resume_diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    recovered: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "autonomous_resume_diagnostic",
        "taxonomy_version": _AUTONOMOUS_RESUME_DIAGNOSTIC_VERSION,
        "code": code,
        "message": message,
        "severity": severity,
        "recovered": recovered,
        "at": _utc_now(),
    }
    if isinstance(details, dict) and details:
        payload["details"] = details
    return payload


def _write_text_if_changed(ws: Workspace, rel_path: str, content: str) -> None:
    if ws.exists(rel_path):
        try:
            if ws.read_text(rel_path) == content:
                return
        except Exception:
            pass
    ws.write_text(rel_path, content)


def _write_json_if_changed(
    ws: Workspace,
    rel_path: str,
    payload: dict[str, Any],
    *,
    ignore_keys: tuple[str, ...] = (),
) -> None:
    if not ws.exists(rel_path):
        ws.write_text(rel_path, json_dumps(payload))
        return

    existing_payload: dict[str, Any] | None = None
    try:
        existing_raw = ws.read_text(rel_path)
        parsed = json.loads(existing_raw)
        if isinstance(parsed, dict):
            existing_payload = parsed
    except Exception:
        existing_payload = None

    if isinstance(existing_payload, dict):
        if ignore_keys:
            comparable_existing = dict(existing_payload)
            comparable_next = dict(payload)
            for key in ignore_keys:
                comparable_existing.pop(key, None)
                comparable_next.pop(key, None)
            if comparable_existing == comparable_next:
                return
        elif existing_payload == payload:
            return

    ws.write_text(rel_path, json_dumps(payload))


def _safe_json_read(ws: Workspace, rel_path: str) -> dict[str, Any] | None:
    if not ws.exists(rel_path):
        return None
    try:
        payload = json.loads(ws.read_text(rel_path))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _recover_attempts_from_artifacts(ws: Workspace) -> list[dict[str, Any]]:
    report_payload = _safe_json_read(ws, AUTONOMOUS_REPORT_JSON)
    if not isinstance(report_payload, dict):
        return []
    attempts = report_payload.get("attempts")
    if not isinstance(attempts, list):
        return []
    return [item for item in attempts if isinstance(item, dict)]


def _normalize_state_for_resume(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(state)
    diagnostics: list[dict[str, Any]] = []

    attempts_raw = normalized.get("attempts")
    if not isinstance(attempts_raw, list):
        attempts_raw = []
        diagnostics.append(
            _make_resume_diagnostic(
                "resume.state.attempts_invalid",
                "state attempts payload was invalid and reset to empty list",
                details={"field": "attempts"},
            )
        )

    deduped: dict[int, dict[str, Any]] = {}
    invalid_attempts = 0
    duplicate_attempts = 0
    for item in attempts_raw:
        if not isinstance(item, dict):
            invalid_attempts += 1
            continue
        iteration = _as_int(item.get("iteration"))
        if iteration is None or iteration <= 0:
            invalid_attempts += 1
            continue
        if iteration in deduped:
            duplicate_attempts += 1
        deduped[iteration] = item

    attempts = [deduped[idx] for idx in sorted(deduped.keys())]
    if invalid_attempts > 0:
        diagnostics.append(
            _make_resume_diagnostic(
                "resume.state.attempts_invalid_entries_dropped",
                "invalid attempt entries were dropped during resume normalization",
                details={"dropped": invalid_attempts},
            )
        )
    if duplicate_attempts > 0:
        diagnostics.append(
            _make_resume_diagnostic(
                "resume.state.attempts_deduplicated",
                "duplicate attempt iterations detected; latest record kept per iteration",
                details={"deduplicated": duplicate_attempts},
            )
        )

    normalized["attempts"] = attempts
    attempt_max_iteration = max([_as_int(a.get("iteration")) or 0 for a in attempts], default=0)
    current_iteration = _as_int(normalized.get("current_iteration")) or 0
    normalized_iteration = max(current_iteration, attempt_max_iteration)
    if normalized_iteration > 0 and not attempts and current_iteration > 0:
        normalized_iteration = current_iteration
    if normalized_iteration != current_iteration:
        diagnostics.append(
            _make_resume_diagnostic(
                "resume.state.current_iteration_aligned",
                "current_iteration aligned to highest persisted attempt index",
                details={"before": current_iteration, "after": normalized_iteration},
            )
        )
    normalized["current_iteration"] = normalized_iteration

    latest_strategy = _latest_strategy_from_attempts(attempts)
    if isinstance(latest_strategy, dict):
        normalized["last_strategy"] = latest_strategy

    gate_attempts = [a for a in attempts if isinstance(a.get("gate_results"), dict)]
    if gate_attempts:
        normalized["last_gate_results"] = gate_attempts[-1].get("gate_results")

    guard_attempts = [a for a in attempts if isinstance(a.get("guard_decision"), dict)]
    if guard_attempts:
        normalized["guard_decision"] = guard_attempts[-1].get("guard_decision")

    existing_diags = normalized.get("resume_diagnostics")
    merged_diags: list[dict[str, Any]] = []
    if isinstance(existing_diags, list):
        merged_diags.extend([item for item in existing_diags if isinstance(item, dict)])
    merged_diags.extend(diagnostics)
    normalized["resume_diagnostics"] = merged_diags
    return normalized, diagnostics


def _load_state_for_resume(ws: Workspace) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not ws.exists(AUTONOMOUS_STATE_FILE):
        return None, []
    try:
        payload = json.loads(ws.read_text(AUTONOMOUS_STATE_FILE))
    except Exception as e:
        return None, [
            _make_resume_diagnostic(
                "resume.state.invalid_json",
                "state file is not valid JSON; recovery fallback will be used",
                details={"error": str(e)},
            )
        ]
    if not isinstance(payload, dict):
        return None, [
            _make_resume_diagnostic(
                "resume.state.invalid_format",
                "state file has invalid format; expected JSON object",
            )
        ]
    normalized, diagnostics = _normalize_state_for_resume(payload)
    return normalized, diagnostics


def _load_state(ws: Workspace) -> dict[str, Any] | None:
    payload, diagnostics = _load_state_for_resume(ws)
    if payload is not None:
        return payload
    if diagnostics:
        first = diagnostics[0]
        if isinstance(first, dict):
            raise SystemExit(f"Failed to load autonomous state: {first.get('message')}")
        raise SystemExit("Failed to load autonomous state")
    return None


def _write_state(ws: Workspace, state: dict[str, Any]) -> None:
    if not isinstance(state.get("resume_diagnostics"), list):
        state["resume_diagnostics"] = []
    payload = dict(state)
    payload["updated_at"] = _utc_now()
    _write_json_if_changed(ws, AUTONOMOUS_STATE_FILE, payload, ignore_keys=("updated_at",))


def _write_gate_results_artifact(
    ws: Workspace,
    *,
    policy: AutonomousQualityGatePolicy,
    attempts: list[dict[str, Any]],
) -> None:
    gate_attempts = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        gate_results = item.get("gate_results")
        if not isinstance(gate_results, dict):
            continue
        gate_attempts.append(
            {
                "iteration": item.get("iteration"),
                "ok": bool(item.get("ok")),
                "gate_results": gate_results,
                "reason": item.get("reason"),
            }
        )

    payload = {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "policy": asdict(policy),
        "attempts": gate_attempts,
    }
    _write_json_if_changed(ws, AUTONOMOUS_GATE_RESULTS_JSON, payload, ignore_keys=("updated_at",))


def _make_budget_guard_decision(
    *,
    reason_code: str,
    message: str,
    policy: AutonomousBudgetGuardPolicy,
    elapsed_seconds: int,
    current_iteration: int,
) -> dict[str, Any]:
    return {
        "type": "autonomous_budget_guard",
        "taxonomy_version": _AUTONOMOUS_BUDGET_GUARD_DIAGNOSTIC_VERSION,
        "decision": "stop",
        "reason_code": reason_code,
        "message": message,
        "triggered_at": _utc_now(),
        "elapsed_seconds": elapsed_seconds,
        "current_iteration": current_iteration,
        "limits": {
            "max_wall_clock_seconds": policy.max_wall_clock_seconds,
            "max_autonomous_iterations": policy.max_autonomous_iterations,
            "max_estimated_token_budget": policy.max_estimated_token_budget,
        },
    }


def _make_budget_guard_snapshot(
    *,
    policy: AutonomousBudgetGuardPolicy,
    elapsed_seconds: int,
    current_iteration: int,
    decision: dict[str, Any] | None = None,
    llm_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed_total_tokens = None
    if isinstance(llm_usage, dict):
        maybe_tokens = _as_int(llm_usage.get("total_input_tokens"))
        maybe_output_tokens = _as_int(llm_usage.get("total_output_tokens"))
        if maybe_tokens is not None and maybe_output_tokens is not None:
            observed_total_tokens = maybe_tokens + maybe_output_tokens

    diagnostics: list[dict[str, Any]] = []
    estimated_status = "not_configured"
    if policy.max_estimated_token_budget is not None:
        estimated_status = "not_available"
        diagnostics.append(
            {
                "type": "autonomous_budget_guard_diagnostic",
                "taxonomy_version": _AUTONOMOUS_BUDGET_GUARD_DIAGNOSTIC_VERSION,
                "code": "budget_guard.estimated_tokens.not_available",
                "reason_code": "autonomous_budget_guard.estimated_token_budget_not_available",
                "severity": "info",
                "message": "estimated token signal is not available; max_estimated_token_budget is not enforced",
                "at": _utc_now(),
                "details": {
                    "configured_max_estimated_token_budget": policy.max_estimated_token_budget,
                    "observed_total_tokens": observed_total_tokens,
                },
            }
        )

    wall_status = "within_limit"
    if decision is not None and decision.get("reason_code") == "autonomous_budget_guard.max_wall_clock_seconds_exceeded":
        wall_status = "exceeded"

    iteration_status = "within_limit"
    if decision is not None and decision.get("reason_code") == "autonomous_budget_guard.max_autonomous_iterations_reached":
        iteration_status = "reached"

    return {
        "type": "autonomous_budget_guard",
        "taxonomy_version": _AUTONOMOUS_BUDGET_GUARD_DIAGNOSTIC_VERSION,
        "status": "triggered" if decision is not None else "within_budget",
        "triggered": decision is not None,
        "decision": decision,
        "checks": {
            "wall_clock": {
                "limit_seconds": policy.max_wall_clock_seconds,
                "elapsed_seconds": elapsed_seconds,
                "status": wall_status,
            },
            "iterations": {
                "limit": policy.max_autonomous_iterations,
                "current": current_iteration,
                "status": iteration_status,
            },
            "estimated_tokens": {
                "limit": policy.max_estimated_token_budget,
                "estimated_total_tokens": None,
                "observed_total_tokens": observed_total_tokens,
                "status": estimated_status,
            },
        },
        "diagnostics": diagnostics,
        "updated_at": _utc_now(),
    }


def _infer_operator_guidance_family(code: str) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return "unknown"
    if normalized.startswith("autonomous_preflight."):
        return "preflight"
    if normalized.startswith("autonomous_budget_guard."):
        return "budget_guard"
    if normalized.startswith("autonomous_guard."):
        return "guard"
    head = normalized.split(".", 1)[0].lower()
    if head in {"tests", "security", "performance"}:
        return "gate"
    return "unknown"


def _resolve_operator_guidance_entry(code: str) -> dict[str, Any]:
    normalized = str(code or "").strip()
    mapped = _OPERATOR_GUIDANCE_BY_CODE.get(normalized)
    if mapped is not None:
        family = str(mapped.get("family") or "unknown")
        title = str(mapped.get("title") or "Operator guidance")
        playbook_anchor = str(mapped.get("playbook_anchor") or _OPERATOR_GUIDANCE_FAMILY_FALLBACK["unknown"]["playbook_anchor"])
        actions = [str(item) for item in mapped.get("actions", []) if item]
        source = "exact"
    else:
        family = _infer_operator_guidance_family(normalized)
        fallback = _OPERATOR_GUIDANCE_FAMILY_FALLBACK.get(family) or _OPERATOR_GUIDANCE_FAMILY_FALLBACK["unknown"]
        title = str(fallback.get("title") or "Operator guidance")
        playbook_anchor = str(fallback.get("playbook_anchor") or _OPERATOR_GUIDANCE_FAMILY_FALLBACK["unknown"]["playbook_anchor"])
        actions = [str(item) for item in fallback.get("actions", []) if item]
        source = "family_fallback" if family != "unknown" else "generic_fallback"

    return {
        "code": normalized,
        "family": family,
        "source": source,
        "title": title,
        "playbook_doc": _AUTONOMOUS_FAILURE_PLAYBOOK_DOC,
        "playbook_anchor": playbook_anchor,
        "playbook_url": f"{_AUTONOMOUS_FAILURE_PLAYBOOK_DOC}{playbook_anchor}",
        "actions": actions,
    }


def _build_operator_guidance(reason_codes: list[str]) -> dict[str, Any]:
    unique_codes: list[str] = []
    seen: set[str] = set()
    for item in reason_codes:
        code = str(item or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        unique_codes.append(code)

    entries = [_resolve_operator_guidance_entry(code) for code in unique_codes]
    if not entries:
        entries = [_resolve_operator_guidance_entry("autonomous.unmapped_or_missing_code")]

    return {
        "taxonomy_version": _AUTONOMOUS_OPERATOR_GUIDANCE_VERSION,
        "playbook_doc": _AUTONOMOUS_FAILURE_PLAYBOOK_DOC,
        "total_codes": len(unique_codes),
        "resolved": entries,
        "top": entries[:3],
    }


def _collect_operator_reason_codes(state: dict[str, Any], attempts: list[dict[str, Any]]) -> list[str]:
    reason_codes: list[str] = []

    preflight = state.get("preflight") if isinstance(state.get("preflight"), dict) else None
    if isinstance(preflight, dict):
        preflight_codes = preflight.get("reason_codes")
        if isinstance(preflight_codes, list):
            reason_codes.extend([str(code) for code in preflight_codes if code])

    budget_guard = state.get("budget_guard") if isinstance(state.get("budget_guard"), dict) else None
    if isinstance(budget_guard, dict):
        decision = budget_guard.get("decision") if isinstance(budget_guard.get("decision"), dict) else None
        if isinstance(decision, dict) and decision.get("reason_code"):
            reason_codes.append(str(decision.get("reason_code")))
        diagnostics = budget_guard.get("diagnostics")
        if isinstance(diagnostics, list):
            for item in diagnostics:
                if isinstance(item, dict) and item.get("reason_code"):
                    reason_codes.append(str(item.get("reason_code")))

    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        gate_results = attempt.get("gate_results") if isinstance(attempt.get("gate_results"), dict) else None
        if isinstance(gate_results, dict):
            fail_reasons = gate_results.get("fail_reasons")
            if isinstance(fail_reasons, list):
                for row in fail_reasons:
                    if isinstance(row, dict) and row.get("code"):
                        reason_codes.append(str(row.get("code")))
        guard_decision = attempt.get("guard_decision") if isinstance(attempt.get("guard_decision"), dict) else None
        if isinstance(guard_decision, dict) and guard_decision.get("reason_code"):
            reason_codes.append(str(guard_decision.get("reason_code")))

    return reason_codes


def _collect_operator_reason_codes_from_summary(
    *,
    preflight_reason_codes: list[str],
    budget_guard_reason_codes: list[str],
    dominant_fail_codes: list[dict[str, Any]],
    guard_decision: dict[str, Any] | None,
) -> list[str]:
    reason_codes: list[str] = []
    reason_codes.extend([str(code) for code in preflight_reason_codes if code])
    reason_codes.extend([str(code) for code in budget_guard_reason_codes if code])

    for item in dominant_fail_codes:
        if isinstance(item, dict) and item.get("code"):
            reason_codes.append(str(item.get("code")))

    if isinstance(guard_decision, dict) and guard_decision.get("reason_code"):
        reason_codes.append(str(guard_decision.get("reason_code")))

    return reason_codes


def _resolve_incident_routing_entry(code: str) -> dict[str, str]:
    normalized = str(code or "").strip()
    family = _infer_operator_guidance_family(normalized)
    mapped = _INCIDENT_ROUTING_BY_CODE.get(normalized)
    if mapped is not None:
        source = "exact"
        route = mapped
    else:
        route = _INCIDENT_ROUTING_FAMILY_FALLBACK.get(family) or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]
        source = "family_fallback" if family != "unknown" else "generic_fallback"

    return {
        "code": normalized,
        "family": family,
        "source": source,
        "owner_team": str(route.get("owner_team") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["owner_team"]),
        "severity": str(route.get("severity") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["severity"]),
        "target_sla": str(route.get("target_sla") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["target_sla"]),
        "escalation_class": str(route.get("escalation_class") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["escalation_class"]),
    }


def _build_incident_routing(reason_codes: list[str]) -> dict[str, Any]:
    unique_codes: list[str] = []
    seen: set[str] = set()
    for item in reason_codes:
        code = str(item or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        unique_codes.append(code)

    resolved = [_resolve_incident_routing_entry(code) for code in unique_codes]
    if not resolved:
        resolved = [_resolve_incident_routing_entry("autonomous.unmapped_or_missing_code")]

    top = resolved[:3]
    primary = top[0] if top else _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]

    return {
        "taxonomy_version": _AUTONOMOUS_INCIDENT_ROUTING_VERSION,
        "total_codes": len(unique_codes),
        "resolved": resolved,
        "top": top,
        "primary": {
            "owner_team": str(primary.get("owner_team") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["owner_team"]),
            "severity": str(primary.get("severity") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["severity"]),
            "target_sla": str(primary.get("target_sla") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["target_sla"]),
            "escalation_class": str(primary.get("escalation_class") or _INCIDENT_ROUTING_FAMILY_FALLBACK["unknown"]["escalation_class"]),
        },
    }


def _render_report(state: dict[str, Any], *, ok: bool, last_validation: Any) -> tuple[dict[str, Any], str]:
    attempts = state.get("attempts") if isinstance(state.get("attempts"), list) else []
    gate_attempts = [a for a in attempts if isinstance(a, dict) and isinstance(a.get("gate_results"), dict)]
    latest_gate_results = gate_attempts[-1].get("gate_results") if gate_attempts else None
    latest_strategy = _latest_strategy_from_attempts(attempts)
    guard_decisions = [
        a.get("guard_decision")
        for a in attempts
        if isinstance(a, dict) and isinstance(a.get("guard_decision"), dict)
    ]
    latest_guard_decision = guard_decisions[-1] if guard_decisions else None
    resume_diagnostics = [
        d for d in (state.get("resume_diagnostics") or []) if isinstance(d, dict)
    ]
    preflight = state.get("preflight") if isinstance(state.get("preflight"), dict) else None
    budget_guard = state.get("budget_guard") if isinstance(state.get("budget_guard"), dict) else None
    operator_reason_codes = _collect_operator_reason_codes(state, attempts)
    operator_guidance = _build_operator_guidance(operator_reason_codes)
    incident_routing = _build_incident_routing(operator_reason_codes)
    report = {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "mode": "autonomous_v1",
        "ok": ok,
        "run_id": state.get("run_id"),
        "request_id": state.get("request_id"),
        "run_out": state.get("run_out"),
        "profile": state.get("profile"),
        "iterations_total": len(attempts),
        "iterations_ok": len([a for a in attempts if isinstance(a, dict) and a.get("ok") is True]),
        "iterations_failed": len([a for a in attempts if isinstance(a, dict) and a.get("ok") is False]),
        "iterations_gate_failed": len([
            a
            for a in gate_attempts
            if isinstance(a, dict)
            and isinstance(a.get("gate_results"), dict)
            and a["gate_results"].get("passed") is False
        ]),
        "policy": state.get("policy"),
        "preflight": preflight,
        "budget_guard": budget_guard,
        "gate_results": latest_gate_results,
        "latest_strategy": latest_strategy,
        "guard_decision": latest_guard_decision,
        "guard_decisions_total": len(guard_decisions),
        "operator_guidance": operator_guidance,
        "incident_routing": incident_routing,
        "resume_diagnostics": resume_diagnostics,
        "resume_warning_count": len(resume_diagnostics),
        "last_validation": last_validation,
        "attempts": attempts,
        "completed_at": _utc_now(),
    }
    md = [
        "# Autonomous Mode Report",
        "",
        f"- Result: {'OK' if ok else 'FAILED'}",
        f"- Run ID: `{state.get('run_id', '')}`",
        f"- Request ID: `{state.get('request_id', '')}`",
        f"- Run directory: `{state.get('run_out', '')}`",
        f"- Profile: `{state.get('profile', '')}`",
        f"- Iterations: `{len(attempts)}`",
    ]
    if preflight is not None:
        preflight_codes = preflight.get("reason_codes") if isinstance(preflight.get("reason_codes"), list) else []
        codes_text = ",".join([str(code) for code in preflight_codes if code]) or "-"
        md.append(
            f"- Preflight: `{str(preflight.get('status') or 'unknown').upper()}` "
            f"(codes={codes_text})"
        )
    if budget_guard is not None:
        guard_status = str(budget_guard.get("status") or "unknown").upper()
        guard_decision = budget_guard.get("decision") if isinstance(budget_guard.get("decision"), dict) else None
        guard_reason = guard_decision.get("reason_code") if isinstance(guard_decision, dict) else "-"
        md.append(f"- Budget guard: `{guard_status}` (reason_code={guard_reason})")

    md.extend([
        "",
        "## Attempts",
    ])
    for item in attempts:
        if not isinstance(item, dict):
            continue
        gate_results = item.get("gate_results") if isinstance(item.get("gate_results"), dict) else None
        strategy = item.get("strategy") if isinstance(item.get("strategy"), dict) else None
        strategy_text = ""
        if strategy is not None:
            strategy_text = f", strategy={strategy.get('name')}"
            if strategy.get("rotation_applied"):
                strategy_text += ", strategy_rotation=applied"
        gate_text = ""
        if gate_results is not None:
            gate_text = f", gate={'PASS' if gate_results.get('passed') else 'FAIL'}"
            fail_reasons = gate_results.get("fail_reasons")
            if isinstance(fail_reasons, list):
                codes = [str(r.get("code")) for r in fail_reasons if isinstance(r, dict) and r.get("code")]
                if codes:
                    gate_text += f", gate_fail_codes={','.join(codes)}"
        guard_text = ""
        guard_decision = item.get("guard_decision") if isinstance(item.get("guard_decision"), dict) else None
        if guard_decision is not None:
            guard_text = f", guard_decision={guard_decision.get('decision', '-')}, guard_reason_code={guard_decision.get('reason_code', '-')}"
            if guard_decision.get("rollback_recommended") is True:
                guard_text += ", rollback_recommended=true"
        md.append(
            f"- Iteration {item.get('iteration')}: "
            f"`{'OK' if item.get('ok') else 'FAILED'}` "
            f"(resume={item.get('resume')}, reason={item.get('reason', '-')}{strategy_text}{gate_text}{guard_text})"
        )

    if isinstance(report.get("latest_strategy"), dict):
        md.append("")
        md.append("## Latest Auto-fix Strategy")
        md.append("```json")
        md.append(json_dumps(report["latest_strategy"]))
        md.append("```")

    if isinstance(report.get("gate_results"), dict):
        md.append("")
        md.append("## Latest Quality Gate Results")
        md.append("```json")
        md.append(json_dumps(report["gate_results"]))
        md.append("```")

    if isinstance(report.get("guard_decision"), dict):
        md.append("")
        md.append("## Stop Guard Decision")
        md.append("```json")
        md.append(json_dumps(report["guard_decision"]))
        md.append("```")

    if isinstance(report.get("budget_guard"), dict):
        md.append("")
        md.append("## Budget Guard")
        md.append("```json")
        md.append(json_dumps(report["budget_guard"]))
        md.append("```")

    md.append("")
    md.append("## Incident Routing")
    incident_top = incident_routing.get("top") if isinstance(incident_routing.get("top"), list) else []
    if incident_top:
        for entry in incident_top:
            if not isinstance(entry, dict):
                continue
            md.append(
                f"- `{entry.get('code', '-')}` ({entry.get('family', '-')}, source={entry.get('source', '-')}) → "
                f"owner/team={entry.get('owner_team', '-')}, severity={entry.get('severity', '-')}, "
                f"target_sla={entry.get('target_sla', '-')}, escalation_class={entry.get('escalation_class', '-')}"
            )
    else:
        md.append("- No typed failure codes observed. Routed to default manual triage fallback.")

    md.append("")
    md.append("## Operator Guidance")
    guidance_top = operator_guidance.get("top") if isinstance(operator_guidance.get("top"), list) else []
    if guidance_top:
        for entry in guidance_top:
            if not isinstance(entry, dict):
                continue
            actions = entry.get("actions") if isinstance(entry.get("actions"), list) else []
            action_text = "; ".join([str(item) for item in actions if item]) or "See playbook for operator actions."
            md.append(
                f"- `{entry.get('code', '-')}` ({entry.get('family', '-')}, source={entry.get('source', '-')}) — "
                f"{entry.get('title', '-')}. Actions: {action_text} "
                f"[playbook]({entry.get('playbook_url', _AUTONOMOUS_FAILURE_PLAYBOOK_DOC)})"
            )
    else:
        md.append(f"- No typed failure codes observed. See `{_AUTONOMOUS_FAILURE_PLAYBOOK_DOC}` for fallback operations.")

    if isinstance(preflight, dict):
        preflight_diagnostics = preflight.get("diagnostics") if isinstance(preflight.get("diagnostics"), list) else []
        if preflight_diagnostics:
            md.append("")
            md.append("## Preflight Diagnostics")
            for item in preflight_diagnostics:
                if not isinstance(item, dict):
                    continue
                md.append(
                    f"- {item.get('reason_code', '-')}: {item.get('message', '-')} "
                    f"(code={item.get('code', '-')}, severity={item.get('severity', '-')}, retryable={item.get('retryable')})"
                )

    if resume_diagnostics:
        md.append("")
        md.append("## Resume Diagnostics")
        for item in resume_diagnostics:
            md.append(
                f"- {item.get('code', '-')}: {item.get('message', '-')} "
                f"(severity={item.get('severity', '-')}, recovered={item.get('recovered')})"
            )

    md.append("")
    return report, "\n".join(md)


def _build_cli_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="autodev autonomous", description="AutoDev fully autonomous mode")
    sub = ap.add_subparsers(dest="action", required=True)

    start = sub.add_parser("start", help="start or resume unattended autonomous run")
    start.add_argument("--prd", required=True)
    start.add_argument(
        "--out",
        required=True,
        help="Output root directory. A run folder named '<prd-file-stem>_<timestamp>' is created inside it.",
    )
    start.add_argument("--profile", default=None)
    start.add_argument("--config", default="config.yaml")
    start.add_argument("--model", default=None)
    start.add_argument("--resume", action="store_true", help="Resume normal run checkpoint on first autonomous iteration")
    start.add_argument(
        "--resume-state",
        action="store_true",
        help="Resume from existing autonomous state in --run-dir (or inferred run directory)",
    )
    start.add_argument(
        "--run-dir",
        default="",
        help="Existing run directory to resume autonomous state from (must contain .autodev/autonomous_state.json)",
    )
    start.add_argument("--max-iterations", type=int, default=None)
    start.add_argument("--time-budget-sec", type=int, default=None)
    start.add_argument("--max-estimated-token-budget", type=int, default=None)
    start.add_argument("--workspace-allowlist", action="append", default=None)
    start.add_argument("--blocked-paths", action="append", default=None)
    start.add_argument("--allow-docker-build", action="store_true", default=None)
    start.add_argument("--allow-external-side-effects", action="store_true", default=None)
    start.add_argument(
        "--preflight-check-artifact-writable",
        action="store_true",
        default=None,
        help="Enable optional preflight artifact-directory writability check.",
    )

    status = sub.add_parser("status", help="print autonomous state for a run")
    status.add_argument("--run-dir", required=True)

    summary = sub.add_parser("summary", help="print autonomous run summary from artifacts")
    summary.add_argument("--run-dir", required=True)
    summary.add_argument("--format", choices=["json", "text"], default="json")

    return ap


def _start(argv: list[str]) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(["start", *argv])

    try:
        cfg = load_config(args.config)
    except (ValueError, OSError) as e:
        raise SystemExit(str(e)) from e

    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid config: 'profiles' must be an object.")

    profile_name = _resolve_profile_name(args.profile, profiles)
    prof = profiles[profile_name]
    run_cfg = cfg.get("run", {})
    if not isinstance(run_cfg, dict):
        raise SystemExit("Invalid config: 'run' must be an object.")

    budget_cfg = run_cfg.get("budget")
    if budget_cfg is not None and not isinstance(budget_cfg, dict):
        raise SystemExit("Invalid config: run.budget must be an object.")

    policy = _resolve_autonomous_policy(args, run_cfg)
    preflight_policy = _resolve_autonomous_preflight_policy(args, run_cfg)
    quality_gate_policy = _resolve_autonomous_quality_gate_policy(run_cfg)
    stop_guard_policy = _resolve_autonomous_stop_guard_policy(run_cfg)
    budget_guard_policy = _resolve_autonomous_budget_guard_policy(args, run_cfg, policy)

    run_out = str(Path(args.run_dir).expanduser().resolve()) if args.run_dir else _resolve_output_dir(args.prd, args.out)
    ws = Workspace(run_out)

    if args.resume_state:
        loaded, resume_diagnostics = _load_state_for_resume(ws)
        if loaded is None and not resume_diagnostics:
            raise SystemExit(f"--resume-state requested, but state file missing: {run_out}/{AUTONOMOUS_STATE_FILE}")

        state_source = "autonomous_state"
        if loaded is None:
            recovered_attempts = _recover_attempts_from_artifacts(ws)
            state = _new_state(
                run_id=uuid4().hex,
                request_id=uuid4().hex,
                run_out=run_out,
                profile=profile_name,
                policy=policy,
                preflight_policy=preflight_policy,
                quality_gate_policy=quality_gate_policy,
                stop_guard_policy=stop_guard_policy,
                budget_guard_policy=budget_guard_policy,
                prd_path=args.prd,
                config_path=args.config,
            )
            state["attempts"] = recovered_attempts
            if recovered_attempts:
                max_iteration = max([_as_int(a.get("iteration")) or 0 for a in recovered_attempts], default=0)
                state["current_iteration"] = max_iteration
                state["phase"] = "auto_fix_retry"
                resume_diagnostics.append(
                    _make_resume_diagnostic(
                        "resume.state.recovered_from_report_artifact",
                        "state file was invalid; recovered attempt history from autonomous_report artifact",
                        details={"recovered_attempts": len(recovered_attempts), "state_source": state_source},
                    )
                )
            else:
                resume_diagnostics.append(
                    _make_resume_diagnostic(
                        "resume.state.reinitialized",
                        "state file was invalid and no recoverable attempts were found; reinitialized state",
                        details={"state_source": state_source},
                    )
                )
        else:
            state = loaded

        state, _ = _normalize_state_for_resume(state)
        if not isinstance(state.get("resume_diagnostics"), list):
            state["resume_diagnostics"] = []
        state["resume_diagnostics"].extend(resume_diagnostics)
        run_id = str(state.get("run_id") or uuid4().hex)
        request_id = str(state.get("request_id") or uuid4().hex)
    else:
        run_id = uuid4().hex
        request_id = uuid4().hex
        state = _new_state(
            run_id=run_id,
            request_id=request_id,
            run_out=run_out,
            profile=profile_name,
            policy=policy,
            preflight_policy=preflight_policy,
            quality_gate_policy=quality_gate_policy,
            stop_guard_policy=stop_guard_policy,
            budget_guard_policy=budget_guard_policy,
            prd_path=args.prd,
            config_path=args.config,
        )

    preflight = _run_autonomous_preflight(
        ws=ws,
        policy=policy,
        preflight_policy=preflight_policy,
        prd=args.prd,
        config=args.config,
        out_root=args.out,
        run_out=run_out,
    )
    state["preflight"] = preflight
    if preflight.get("ok") is not True:
        state["status"] = "failed"
        state["phase"] = "failed"
        state["failure_reason"] = "preflight_failed"
        _write_state(ws, state)

        report_json, report_md = _render_report(state, ok=False, last_validation=[])
        _write_json_if_changed(ws, AUTONOMOUS_REPORT_JSON, report_json, ignore_keys=("completed_at",))
        _write_text_if_changed(ws, AUTONOMOUS_REPORT_MD, report_md)

        failure_metadata = {
            "run_id": run_id,
            "request_id": request_id,
            "requested_profile": profile_name,
            "autonomous_mode": True,
            "result_ok": False,
            "run_completed_at": _utc_now(),
            "autonomous_policy": {
                "max_iterations": policy.max_iterations,
                "time_budget_sec": policy.time_budget_sec,
                "workspace_allowlist": [_normalize_path(x) for x in policy.workspace_allowlist],
                "blocked_paths": [_normalize_path(x) for x in policy.blocked_paths],
                "allow_docker_build": policy.allow_docker_build,
                "allow_external_side_effects": policy.allow_external_side_effects,
                "preflight_policy": asdict(preflight_policy),
                "budget_guard_policy": asdict(budget_guard_policy),
            },
            "autonomous_preflight": preflight,
            "autonomous_budget_guard": state.get("budget_guard"),
        }
        _write_json_if_changed(ws, ".autodev/run_metadata.json", failure_metadata, ignore_keys=("run_completed_at",))

        print(
            {
                "ok": False,
                "out": os.path.abspath(run_out),
                "iterations": state.get("current_iteration"),
                "max_iterations": policy.max_iterations,
                "preflight": {
                    "status": preflight.get("status"),
                    "reason_codes": preflight.get("reason_codes", []),
                },
            }
        )
        raise SystemExit(1)

    template_candidates = prof["template_candidates"]
    validators_enabled = prof["validators"]
    quality_profile = dict(prof.get("quality_profile", {}))
    per_task_soft = _coerce_optional_str_list(quality_profile.get("per_task_soft"))
    final_soft = _coerce_optional_str_list(quality_profile.get("final_soft"))
    disable_docker_build = bool(prof.get("disable_docker_build", False)) or (not policy.allow_docker_build)

    prd_md = _read_text_file(args.prd, "PRD file")

    llm_cfg = cfg["llm"]
    role_temperatures = _coerce_role_temperatures(llm_cfg.get("role_temperatures"))
    llm_api_key = (llm_cfg.get("api_key") or "").strip()
    llm_oauth_token = (llm_cfg.get("oauth_token") or "").strip()
    if not llm_api_key and not llm_oauth_token:
        raise SystemExit(
            "Missing LLM authentication. Set llm.api_key or llm.oauth_token in config.yaml "
            "(or use ${AUTODEV_LLM_API_KEY}/${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}) "
            "or define AUTODEV_LLM_API_KEY/AUTODEV_CLAUDE_CODE_OAUTH_TOKEN in the environment."
        )

    llm_model = (args.model or os.getenv("AUTODEV_LLM_MODEL") or llm_cfg.get("model") or "").strip()
    if not llm_model:
        raise SystemExit(
            "Missing LLM model. Set llm.model in config.yaml, define AUTODEV_LLM_MODEL, or pass --model."
        )

    router: ModelRouter | None = None
    models_list = llm_cfg.get("models")
    if isinstance(models_list, list) and models_list:
        endpoints: list[ModelEndpoint] = []
        for entry in models_list:
            ep_api_key = (entry.get("api_key") or "").strip() or None
            ep_oauth = (entry.get("oauth_token") or "").strip() or None
            endpoints.append(
                ModelEndpoint(
                    base_url=entry["base_url"],
                    model=entry["model"],
                    api_key=ep_api_key,
                    oauth_token=ep_oauth,
                )
            )
        role_mapping_raw = llm_cfg.get("role_mapping")
        role_mapping_parsed: Dict[str, int] = {}
        if isinstance(role_mapping_raw, dict):
            role_mapping_parsed = {str(k): int(v) for k, v in role_mapping_raw.items()}
        router = ModelRouter(endpoints=endpoints, role_mapping=role_mapping_parsed)

    max_parallel_tasks = _coerce_max_parallel_tasks(run_cfg.get("max_parallel_tasks"), default=2)
    max_token_budget = None
    if isinstance(budget_cfg, dict):
        max_token_budget = _coerce_optional_int(budget_cfg.get("max_tokens"), "budget.max_tokens")
        if max_token_budget is not None and max_token_budget <= 0:
            raise SystemExit("config.run.budget.max_tokens must be a positive integer.")

    client = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=llm_api_key or None,
        oauth_token=llm_oauth_token or None,
        model=llm_model,
        timeout_sec=int(llm_cfg.get("timeout_sec", 240)),
        max_total_tokens=max_token_budget,
        router=router,
    )

    template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))

    run_metadata = {
        "run_id": run_id,
        "request_id": request_id,
        "requested_profile": profile_name,
        "autonomous_mode": True,
        "autonomous_policy": {
            "max_iterations": policy.max_iterations,
            "time_budget_sec": policy.time_budget_sec,
            "workspace_allowlist": [_normalize_path(x) for x in policy.workspace_allowlist],
            "blocked_paths": [_normalize_path(x) for x in policy.blocked_paths],
            "allow_docker_build": policy.allow_docker_build,
            "allow_external_side_effects": policy.allow_external_side_effects,
            "preflight_policy": asdict(preflight_policy),
        },
        "quality_profile": quality_profile,
        "template_candidates": template_candidates,
        "per_task_soft_validators": per_task_soft,
        "final_soft_validators": final_soft,
        "disable_docker_build": disable_docker_build,
        "validators_enabled": validators_enabled,
        "max_parallel_tasks": max_parallel_tasks,
        "llm": {
            "model": llm_model,
            "auth_source": "api_key" if llm_api_key else "oauth_token",
            "model_override": {
                "cli": args.model,
                "env": os.getenv("AUTODEV_LLM_MODEL"),
            },
            "budget": {
                "max_total_tokens": max_token_budget,
            },
        },
        "role_temperatures": role_temperatures,
    }
    if quality_gate_policy is not None:
        run_metadata["autonomous_quality_gate_policy"] = asdict(quality_gate_policy)
    run_metadata["autonomous_stop_guard_policy"] = asdict(stop_guard_policy)
    run_metadata["autonomous_budget_guard_policy"] = asdict(budget_guard_policy)
    run_metadata["autonomous_preflight"] = preflight
    _write_json_if_changed(ws, ".autodev/run_metadata.json", run_metadata)
    state["budget_guard"] = _make_budget_guard_snapshot(
        policy=budget_guard_policy,
        elapsed_seconds=0,
        current_iteration=int(state.get("current_iteration") or 0),
    )
    _write_state(ws, state)

    start_monotonic = time.monotonic()
    ok = False
    prd_struct: Dict[str, Any] = {}
    plan: Dict[str, Any] = {}
    last_validation: Any = []

    attempt_index = int(state.get("current_iteration", 0))
    explicit_resume_first = bool(args.resume)
    while attempt_index < policy.max_iterations:
        elapsed = int(time.monotonic() - start_monotonic)
        if elapsed >= policy.time_budget_sec:
            budget_guard_decision = _make_budget_guard_decision(
                reason_code="autonomous_budget_guard.max_wall_clock_seconds_exceeded",
                message="wall-clock budget exhausted before next autonomous iteration",
                policy=budget_guard_policy,
                elapsed_seconds=elapsed,
                current_iteration=int(state.get("current_iteration") or 0),
            )
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "time_budget_exceeded"
            state["budget_guard"] = _make_budget_guard_snapshot(
                policy=budget_guard_policy,
                elapsed_seconds=elapsed,
                current_iteration=int(state.get("current_iteration") or 0),
                decision=budget_guard_decision,
            )
            _write_state(ws, state)
            break

        attempt_index += 1
        state["current_iteration"] = attempt_index
        state["phase"] = "plan"
        state["budget_guard"] = _make_budget_guard_snapshot(
            policy=budget_guard_policy,
            elapsed_seconds=elapsed,
            current_iteration=attempt_index,
        )
        _write_state(ws, state)

        resume_flag = explicit_resume_first if attempt_index == 1 else True
        attempts = state.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
            state["attempts"] = attempts

        strategy = _resolve_retry_strategy(attempts, attempt_index)
        state["last_strategy"] = strategy

        _log_event(
            "autonomous.iteration_start",
            run_id=run_id,
            request_id=request_id,
            profile=profile_name,
            run_out=run_out,
            iteration=attempt_index,
            resume=resume_flag,
            strategy=strategy.get("name"),
            strategy_recommended=strategy.get("recommended"),
            strategy_rotation_applied=bool(strategy.get("rotation_applied")),
        )

        attempt_record: dict[str, Any] = {
            "iteration": attempt_index,
            "started_at": _utc_now(),
            "resume": resume_flag,
            "strategy": strategy,
        }

        workflow_error: ValueError | None = None
        try:
            state["phase"] = "execute"
            _write_state(ws, state)
            attempt_quality_profile = dict(quality_profile)
            attempt_quality_profile["autonomous_fix_strategy"] = {
                "name": strategy.get("name"),
                "hints": strategy.get("hints", []),
                "recommended": strategy.get("recommended"),
                "recommended_hints": strategy.get("recommended_hints", []),
                "rationale": strategy.get("rationale"),
                "selected_by": strategy.get("selected_by"),
                "rotation_applied": strategy.get("rotation_applied"),
                "rotation_reason": strategy.get("rotation_reason"),
                "gate_fail_codes": strategy.get("gate_fail_codes", []),
                "gate_categories": strategy.get("gate_categories", []),
                "gate_names": strategy.get("gate_names", []),
            }
            ok, prd_struct, plan, last_validation = asyncio.run(
                run_autodev_enterprise(
                    client=client,
                    ws=ws,
                    prd_markdown=prd_md,
                    template_root=template_root,
                    template_candidates=template_candidates,
                    validators_enabled=validators_enabled,
                    audit_required=bool(prof.get("security", {}).get("audit_required", False)),
                    max_fix_loops_total=_coerce_int(run_cfg.get("max_fix_loops_total"), "max_fix_loops_total", 10),
                    max_fix_loops_per_task=_coerce_int(run_cfg.get("max_fix_loops_per_task"), "max_fix_loops_per_task", 4),
                    max_json_repair=_coerce_int(run_cfg.get("max_json_repair"), "max_json_repair", 2),
                    task_soft_validators=per_task_soft,
                    final_soft_validators=final_soft,
                    quality_profile=attempt_quality_profile,
                    disable_docker_build=disable_docker_build,
                    verbose=bool(run_cfg.get("verbose", True)),
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile_name,
                    resume=resume_flag,
                    interactive=False,
                    role_temperatures=role_temperatures,
                    max_parallel_tasks=max_parallel_tasks,
                    progress_callback=make_cli_progress_callback(),
                )
            )
        except ValueError as e:
            workflow_error = e
            ok = False

        gate_results: dict[str, Any] | None = None
        if workflow_error is None and quality_gate_policy is not None:
            gate_results = _evaluate_quality_gates(
                ws=ws,
                policy=quality_gate_policy,
                last_validation=last_validation,
            )
            attempt_record["gate_results"] = gate_results
            if gate_results.get("passed") is False:
                ok = False
                attempt_record["quality_gate_failed"] = True
                attempt_record["quality_gate_fail_reasons"] = gate_results.get("fail_reasons", [])
                attempt_record["reason"] = "quality_gate_failed"

        attempt_record["ended_at"] = _utc_now()
        attempt_record["ok"] = ok
        if workflow_error is not None:
            attempt_record["reason"] = str(workflow_error)
        attempts.append(attempt_record)
        state["last_strategy"] = strategy
        _write_strategy_trace_artifact(ws, attempts)

        if gate_results is not None:
            state["last_gate_results"] = gate_results
            _write_gate_results_artifact(ws, policy=quality_gate_policy, attempts=attempts)

        guard_decision = _evaluate_stop_guard_decision(attempts, stop_guard_policy)
        if isinstance(guard_decision, dict):
            ok = False
            attempt_record["ok"] = False
            if not attempt_record.get("reason"):
                attempt_record["reason"] = "autonomous_guard_stop"
            attempt_record["guard_decision"] = guard_decision
            state["guard_decision"] = guard_decision
            state["failure_reason"] = "autonomous_guard_stop"
            _write_guard_decisions_artifact(ws, policy=stop_guard_policy, attempts=attempts)
        elif any(isinstance(a, dict) and isinstance(a.get("guard_decision"), dict) for a in attempts):
            _write_guard_decisions_artifact(ws, policy=stop_guard_policy, attempts=attempts)

        elapsed_after_attempt = int(time.monotonic() - start_monotonic)
        state["budget_guard"] = _make_budget_guard_snapshot(
            policy=budget_guard_policy,
            elapsed_seconds=elapsed_after_attempt,
            current_iteration=int(state.get("current_iteration") or 0),
        )
        _write_state(ws, state)

        if ok:
            state["status"] = "completed"
            state["phase"] = "completed"
            _write_state(ws, state)
            break

        if isinstance(attempt_record.get("guard_decision"), dict):
            state["status"] = "failed"
            state["phase"] = "failed"
            _write_state(ws, state)
            break

        if attempt_index >= policy.max_iterations:
            elapsed_on_stop = int(time.monotonic() - start_monotonic)
            budget_guard_decision = _make_budget_guard_decision(
                reason_code="autonomous_budget_guard.max_autonomous_iterations_reached",
                message="autonomous iteration budget reached",
                policy=budget_guard_policy,
                elapsed_seconds=elapsed_on_stop,
                current_iteration=attempt_index,
            )
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "max_iterations_exceeded"
            state["budget_guard"] = _make_budget_guard_snapshot(
                policy=budget_guard_policy,
                elapsed_seconds=elapsed_on_stop,
                current_iteration=attempt_index,
                decision=budget_guard_decision,
            )
            _write_state(ws, state)
            break

        state["phase"] = "auto_fix_retry"
        _write_state(ws, state)

    llm_usage = client.usage_summary()
    existing_budget_guard_decision = None
    existing_budget_guard = state.get("budget_guard") if isinstance(state.get("budget_guard"), dict) else None
    if isinstance(existing_budget_guard, dict) and isinstance(existing_budget_guard.get("decision"), dict):
        existing_budget_guard_decision = existing_budget_guard.get("decision")
    state["budget_guard"] = _make_budget_guard_snapshot(
        policy=budget_guard_policy,
        elapsed_seconds=int(time.monotonic() - start_monotonic),
        current_iteration=int(state.get("current_iteration") or 0),
        decision=existing_budget_guard_decision,
        llm_usage=llm_usage,
    )
    _write_state(ws, state)

    run_metadata["llm_usage"] = llm_usage
    run_metadata["result_ok"] = bool(ok)
    run_metadata["run_completed_at"] = _utc_now()
    run_metadata["autonomous_latest_strategy"] = state.get("last_strategy")
    run_metadata["autonomous_strategy_trace_path"] = AUTONOMOUS_STRATEGY_TRACE_JSON
    run_metadata["autonomous_guard_decisions_path"] = AUTONOMOUS_GUARD_DECISIONS_JSON
    run_metadata["autonomous_guard_decision"] = state.get("guard_decision")
    run_metadata["autonomous_budget_guard"] = state.get("budget_guard")
    _write_json_if_changed(ws, ".autodev/run_metadata.json", run_metadata, ignore_keys=("run_completed_at",))

    attempts_for_artifact = state.get("attempts") if isinstance(state.get("attempts"), list) else []
    _write_guard_decisions_artifact(ws, policy=stop_guard_policy, attempts=attempts_for_artifact)

    report_json, report_md = _render_report(state, ok=ok, last_validation=last_validation)
    _write_json_if_changed(ws, AUTONOMOUS_REPORT_JSON, report_json, ignore_keys=("completed_at",))
    _write_text_if_changed(ws, AUTONOMOUS_REPORT_MD, report_md)

    write_report(ws.root, prd_struct, plan, last_validation, ok)
    _log_event(
        "autonomous.run_complete",
        run_id=run_id,
        request_id=request_id,
        profile=profile_name,
        ok=ok,
        output_dir=os.path.abspath(run_out),
        iterations=state.get("current_iteration"),
        max_iterations=policy.max_iterations,
        llm_usage=llm_usage,
    )
    print(
        {
            "ok": ok,
            "out": os.path.abspath(run_out),
            "iterations": state.get("current_iteration"),
            "max_iterations": policy.max_iterations,
            "llm_usage": llm_usage,
        }
    )
    if not ok:
        raise SystemExit(1)


def _safe_load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"invalid_json: {e}"
    if not isinstance(payload, dict):
        return None, "invalid_format: expected object"
    return payload, None


def extract_autonomous_summary(run_dir: str) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    artifacts_dir = run_path / ".autodev"

    report_path = artifacts_dir / "autonomous_report.json"
    gate_path = artifacts_dir / "autonomous_gate_results.json"
    strategy_path = artifacts_dir / "autonomous_strategy_trace.json"
    guard_path = artifacts_dir / "autonomous_guard_decisions.json"

    report_payload, report_error = _safe_load_json(report_path)
    gate_payload, gate_error = _safe_load_json(gate_path)
    strategy_payload, strategy_error = _safe_load_json(strategy_path)
    guard_payload, guard_error = _safe_load_json(guard_path)

    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    artifact_status = {
        "report": {"path": str(report_path), "status": "ok" if report_error is None else report_error},
        "gate_results": {"path": str(gate_path), "status": "ok" if gate_error is None else gate_error},
        "strategy_trace": {"path": str(strategy_path), "status": "ok" if strategy_error is None else strategy_error},
        "guard_decisions": {"path": str(guard_path), "status": "ok" if guard_error is None else guard_error},
    }

    for name, err in (
        ("report", report_error),
        ("gate_results", gate_error),
        ("strategy_trace", strategy_error),
        ("guard_decisions", guard_error),
    ):
        if err is not None:
            warnings.append(f"{name}: {err}")
            diagnostics.append(
                {
                    "type": "autonomous_summary_warning",
                    "taxonomy_version": _AUTONOMOUS_RESUME_DIAGNOSTIC_VERSION,
                    "code": "summary.artifact_unavailable",
                    "artifact": name,
                    "severity": "warning",
                    "message": f"{name}: {err}",
                }
            )

    status = "unknown"
    if isinstance(report_payload, dict):
        if isinstance(report_payload.get("ok"), bool):
            status = "completed" if report_payload["ok"] else "failed"
        status = str(report_payload.get("status") or status)

    preflight = report_payload.get("preflight") if isinstance(report_payload, dict) and isinstance(report_payload.get("preflight"), dict) else None
    preflight_status = str(preflight.get("status") or "unknown") if isinstance(preflight, dict) else "unknown"
    preflight_reason_codes = (
        [str(code) for code in preflight.get("reason_codes", []) if code]
        if isinstance(preflight, dict) and isinstance(preflight.get("reason_codes"), list)
        else []
    )

    gate_attempts = gate_payload.get("attempts") if isinstance(gate_payload, dict) else None
    if not isinstance(gate_attempts, list):
        attempts_from_report = report_payload.get("attempts") if isinstance(report_payload, dict) else None
        if isinstance(attempts_from_report, list):
            gate_attempts = [
                item
                for item in attempts_from_report
                if isinstance(item, dict) and isinstance(item.get("gate_results"), dict)
            ]
        else:
            gate_attempts = []

    pass_count = 0
    fail_count = 0
    fail_code_counter: Counter[str] = Counter()

    for attempt in gate_attempts:
        if not isinstance(attempt, dict):
            continue
        gate_results = attempt.get("gate_results") if isinstance(attempt.get("gate_results"), dict) else None
        if gate_results is None:
            continue
        passed = gate_results.get("passed") is True
        if passed:
            pass_count += 1
        else:
            fail_count += 1
        fail_reasons = gate_results.get("fail_reasons")
        if isinstance(fail_reasons, list):
            for reason in fail_reasons:
                if not isinstance(reason, dict):
                    continue
                code = reason.get("code")
                if code:
                    fail_code_counter[str(code)] += 1

    latest_strategy = None
    strategy_source = "none"
    if isinstance(strategy_payload, dict) and isinstance(strategy_payload.get("latest"), dict):
        latest_strategy = strategy_payload["latest"]
        strategy_source = "strategy_trace"
    elif isinstance(report_payload, dict) and isinstance(report_payload.get("latest_strategy"), dict):
        latest_strategy = report_payload["latest_strategy"]
        strategy_source = "report"

    dominant_fail_codes = [
        {"code": code, "count": count}
        for code, count in fail_code_counter.most_common()
    ]

    latest_guard_decision = None
    guard_source = "none"
    guard_decisions_total = 0
    if isinstance(guard_payload, dict):
        decisions = guard_payload.get("decisions")
        if isinstance(decisions, list):
            guard_decisions_total = len([d for d in decisions if isinstance(d, dict)])
        if isinstance(guard_payload.get("latest"), dict):
            latest_guard_decision = guard_payload.get("latest")
            guard_source = "guard_decisions"
    if latest_guard_decision is None and isinstance(report_payload, dict) and isinstance(report_payload.get("guard_decision"), dict):
        latest_guard_decision = report_payload.get("guard_decision")
        guard_source = "report"
        guard_decisions_total = int(report_payload.get("guard_decisions_total") or 0)

    resume_diagnostics = []
    if isinstance(report_payload, dict) and isinstance(report_payload.get("resume_diagnostics"), list):
        resume_diagnostics = [
            item for item in report_payload.get("resume_diagnostics", []) if isinstance(item, dict)
        ]
        diagnostics.extend(resume_diagnostics)

    preflight_diagnostics = []
    if isinstance(preflight, dict) and isinstance(preflight.get("diagnostics"), list):
        preflight_diagnostics = [item for item in preflight.get("diagnostics", []) if isinstance(item, dict)]
        diagnostics.extend(preflight_diagnostics)

    budget_guard = report_payload.get("budget_guard") if isinstance(report_payload, dict) and isinstance(report_payload.get("budget_guard"), dict) else None
    budget_guard_decision = budget_guard.get("decision") if isinstance(budget_guard, dict) and isinstance(budget_guard.get("decision"), dict) else None
    budget_guard_status = str(budget_guard.get("status") or "unknown") if isinstance(budget_guard, dict) else "unknown"
    budget_guard_reason_codes: list[str] = []
    if isinstance(budget_guard_decision, dict) and budget_guard_decision.get("reason_code"):
        budget_guard_reason_codes.append(str(budget_guard_decision.get("reason_code")))
    if isinstance(budget_guard, dict) and isinstance(budget_guard.get("diagnostics"), list):
        for item in budget_guard.get("diagnostics", []):
            if not isinstance(item, dict):
                continue
            diagnostics.append(item)
            if item.get("reason_code"):
                budget_guard_reason_codes.append(str(item.get("reason_code")))
    budget_guard_reason_codes = sorted(set(budget_guard_reason_codes))

    reason_codes = _collect_operator_reason_codes_from_summary(
        preflight_reason_codes=preflight_reason_codes,
        budget_guard_reason_codes=budget_guard_reason_codes,
        dominant_fail_codes=dominant_fail_codes,
        guard_decision=latest_guard_decision if isinstance(latest_guard_decision, dict) else None,
    )

    operator_guidance = None
    if isinstance(report_payload, dict) and isinstance(report_payload.get("operator_guidance"), dict):
        operator_guidance = report_payload.get("operator_guidance")
    if not isinstance(operator_guidance, dict):
        operator_guidance = _build_operator_guidance(reason_codes)

    incident_routing = None
    if isinstance(report_payload, dict) and isinstance(report_payload.get("incident_routing"), dict):
        incident_routing = report_payload.get("incident_routing")
    if not isinstance(incident_routing, dict):
        incident_routing = _build_incident_routing(reason_codes)

    primary_routing = incident_routing.get("primary") if isinstance(incident_routing.get("primary"), dict) else {}

    return {
        "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
        "mode": "autonomous_v1_summary",
        "run_dir": str(run_path),
        "status": status,
        "artifacts": artifact_status,
        "latest_run": {
            "run_id": report_payload.get("run_id") if isinstance(report_payload, dict) else None,
            "request_id": report_payload.get("request_id") if isinstance(report_payload, dict) else None,
            "profile": report_payload.get("profile") if isinstance(report_payload, dict) else None,
            "completed_at": report_payload.get("completed_at") if isinstance(report_payload, dict) else None,
        },
        "preflight": preflight,
        "preflight_status": preflight_status,
        "preflight_reason_codes": preflight_reason_codes,
        "budget_guard": budget_guard,
        "budget_guard_status": budget_guard_status,
        "budget_guard_decision": budget_guard_decision,
        "budget_guard_reason_codes": budget_guard_reason_codes,
        "gate_counts": {
            "pass": pass_count,
            "fail": fail_count,
            "total": pass_count + fail_count,
        },
        "dominant_fail_codes": dominant_fail_codes,
        "latest_strategy": latest_strategy,
        "latest_strategy_source": strategy_source,
        "guard_decision": latest_guard_decision,
        "guard_decision_source": guard_source,
        "guard_decisions_total": guard_decisions_total,
        "operator_guidance": operator_guidance,
        "incident_routing": incident_routing,
        "incident_owner_team": str(primary_routing.get("owner_team") or "-"),
        "incident_severity": str(primary_routing.get("severity") or "-"),
        "incident_target_sla": str(primary_routing.get("target_sla") or "-"),
        "incident_escalation_class": str(primary_routing.get("escalation_class") or "-"),
        "resume_diagnostics": resume_diagnostics,
        "preflight_diagnostics": preflight_diagnostics,
        "warnings": warnings,
        "diagnostics": diagnostics,
    }


def _render_autonomous_summary_text(summary: dict[str, Any]) -> str:
    gate_counts = summary.get("gate_counts") if isinstance(summary.get("gate_counts"), dict) else {}
    dominant_codes = summary.get("dominant_fail_codes") if isinstance(summary.get("dominant_fail_codes"), list) else []
    latest_strategy = summary.get("latest_strategy") if isinstance(summary.get("latest_strategy"), dict) else None
    guard_decision = summary.get("guard_decision") if isinstance(summary.get("guard_decision"), dict) else None
    budget_guard_decision = summary.get("budget_guard_decision") if isinstance(summary.get("budget_guard_decision"), dict) else None
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    operator_guidance = summary.get("operator_guidance") if isinstance(summary.get("operator_guidance"), dict) else {}

    lines = [
        "# Autonomous Run Summary",
        f"- run_dir: {summary.get('run_dir')}",
        f"- status: {summary.get('status')}",
        f"- preflight: {summary.get('preflight_status', 'unknown')}",
        f"- budget_guard: {summary.get('budget_guard_status', 'unknown')}",
        f"- incident_owner_team: {summary.get('incident_owner_team', '-')}",
        f"- incident_severity: {summary.get('incident_severity', '-')}",
        f"- incident_target_sla: {summary.get('incident_target_sla', '-')}",
        f"- incident_escalation_class: {summary.get('incident_escalation_class', '-')}",
        f"- gate_counts: pass={gate_counts.get('pass', 0)}, fail={gate_counts.get('fail', 0)}, total={gate_counts.get('total', 0)}",
    ]

    preflight_reason_codes = (
        summary.get("preflight_reason_codes")
        if isinstance(summary.get("preflight_reason_codes"), list)
        else []
    )
    if preflight_reason_codes:
        lines.append(f"- preflight_reason_codes: {','.join(str(code) for code in preflight_reason_codes)}")
    else:
        lines.append("- preflight_reason_codes: -")

    budget_guard_reason_codes = (
        summary.get("budget_guard_reason_codes")
        if isinstance(summary.get("budget_guard_reason_codes"), list)
        else []
    )
    if budget_guard_reason_codes:
        lines.append(f"- budget_guard_reason_codes: {','.join(str(code) for code in budget_guard_reason_codes)}")
    else:
        lines.append("- budget_guard_reason_codes: -")

    if dominant_codes:
        codes_text = ", ".join(
            [f"{item.get('code')}({item.get('count')})" for item in dominant_codes if isinstance(item, dict)]
        )
        lines.append(f"- dominant_fail_codes: {codes_text}")
    else:
        lines.append("- dominant_fail_codes: -")

    if latest_strategy is not None:
        lines.append(f"- latest_strategy: {latest_strategy.get('name', '-')}")
    else:
        lines.append("- latest_strategy: -")

    if guard_decision is not None:
        lines.append(
            "- guard_decision: "
            f"{guard_decision.get('decision', '-')}"
            f" ({guard_decision.get('reason_code', '-')})"
        )
    else:
        lines.append("- guard_decision: -")

    if budget_guard_decision is not None:
        lines.append(
            "- budget_guard_decision: "
            f"{budget_guard_decision.get('decision', '-')}"
            f" ({budget_guard_decision.get('reason_code', '-')})"
        )
    else:
        lines.append("- budget_guard_decision: -")

    guidance_top = operator_guidance.get("top") if isinstance(operator_guidance.get("top"), list) else []
    if guidance_top:
        lines.append("- operator_guidance_top:")
        for entry in guidance_top:
            if not isinstance(entry, dict):
                continue
            actions = entry.get("actions") if isinstance(entry.get("actions"), list) else []
            top_action = str(actions[0]) if actions else "See playbook"
            lines.append(
                f"  - {entry.get('code', '-')}: {top_action} [{entry.get('playbook_url', _AUTONOMOUS_FAILURE_PLAYBOOK_DOC)}]"
            )
    else:
        lines.append("- operator_guidance_top: -")

    incident_routing = summary.get("incident_routing") if isinstance(summary.get("incident_routing"), dict) else {}
    incident_top = incident_routing.get("top") if isinstance(incident_routing.get("top"), list) else []
    if incident_top:
        lines.append("- incident_routing_top:")
        for entry in incident_top:
            if not isinstance(entry, dict):
                continue
            lines.append(
                "  - "
                f"{entry.get('code', '-')}: owner/team={entry.get('owner_team', '-')}, "
                f"severity={entry.get('severity', '-')}, target_sla={entry.get('target_sla', '-')}, "
                f"escalation_class={entry.get('escalation_class', '-')}"
            )
    else:
        lines.append("- incident_routing_top: -")

    lines.append(f"- guard_decisions_total: {summary.get('guard_decisions_total', 0)}")

    lines.append("")
    lines.append("Artifacts:")
    for name in ("report", "gate_results", "strategy_trace", "guard_decisions"):
        item = artifacts.get(name) if isinstance(artifacts.get(name), dict) else {}
        lines.append(f"- {name}: {item.get('status', 'missing')} ({item.get('path', '-')})")

    warnings = summary.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")

    preflight_diagnostics = summary.get("preflight_diagnostics")
    if isinstance(preflight_diagnostics, list) and preflight_diagnostics:
        lines.append("")
        lines.append("Preflight diagnostics:")
        for item in preflight_diagnostics:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('reason_code', '-')}: {item.get('message', '-')} "
                f"(code={item.get('code', '-')}, severity={item.get('severity', '-')}, retryable={item.get('retryable')})"
            )

    resume_diagnostics = summary.get("resume_diagnostics")
    if isinstance(resume_diagnostics, list) and resume_diagnostics:
        lines.append("")
        lines.append("Resume diagnostics:")
        for item in resume_diagnostics:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- {item.get('code', '-')}: {item.get('message', '-')} "
                f"(severity={item.get('severity', '-')}, recovered={item.get('recovered')})"
            )

    return "\n".join(lines)


def _summary(argv: list[str]) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(["summary", *argv])
    summary = extract_autonomous_summary(args.run_dir)
    if args.format == "text":
        print(_render_autonomous_summary_text(summary))
        return
    print(json_dumps(summary))


def _status(argv: list[str]) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(["status", *argv])
    run_dir = str(Path(args.run_dir).expanduser().resolve())
    ws = Workspace(run_dir)
    state = _load_state(ws)
    if state is None:
        raise SystemExit(f"autonomous state not found: {run_dir}/{AUTONOMOUS_STATE_FILE}")
    print(json_dumps(state))


def cli(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("Usage: autodev autonomous <start|status|summary> ...")
    action = argv[0]
    if action == "start":
        _start(argv[1:])
        return
    if action == "status":
        _status(argv[1:])
        return
    if action == "summary":
        _summary(argv[1:])
        return
    raise SystemExit(f"Unknown autonomous subcommand: {action}")
