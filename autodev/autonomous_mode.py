from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

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
_AUTONOMOUS_GATE_BASELINE_HISTORY_LIMIT = 20

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


@dataclass(frozen=True)
class AutonomousPolicy:
    max_iterations: int
    time_budget_sec: int
    workspace_allowlist: list[str]
    blocked_paths: list[str]
    allow_docker_build: bool
    allow_external_side_effects: bool


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
        "updated_at": _utc_now(),
        "attempts": strategy_attempts,
        "latest": latest,
    }
    ws.write_text(AUTONOMOUS_STRATEGY_TRACE_JSON, json_dumps(payload))


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


def _enforce_path_boundaries(policy: AutonomousPolicy, *, prd: str, config: str, out_root: str, run_out: str) -> None:
    allowlist = [_normalize_path(x) for x in policy.workspace_allowlist]
    blocked = [_normalize_path(x) for x in policy.blocked_paths]
    targets = {
        "prd": _normalize_path(prd),
        "config": _normalize_path(config),
        "out_root": _normalize_path(out_root),
        "run_out": _normalize_path(run_out),
    }

    for label, target in targets.items():
        if not any(_is_under(target, root) for root in allowlist):
            raise SystemExit(
                f"Autonomous policy blocked path '{label}': {target}. "
                f"Not under workspace_allowlist={allowlist}."
            )
        if any(_is_under(target, blocked_root) for blocked_root in blocked):
            raise SystemExit(
                f"Autonomous policy blocked path '{label}': {target}. "
                f"Matches blocked_paths={blocked}."
            )


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
    quality_gate_policy: AutonomousQualityGatePolicy | None,
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
    if quality_gate_policy is not None:
        policy_payload["quality_gate_policy"] = asdict(quality_gate_policy)

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
        "last_strategy": None,
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _load_state(ws: Workspace) -> dict[str, Any] | None:
    if not ws.exists(AUTONOMOUS_STATE_FILE):
        return None
    try:
        raw = ws.read_text(AUTONOMOUS_STATE_FILE)
        payload = json.loads(raw)
    except Exception as e:
        raise SystemExit(f"Failed to load autonomous state: {e}") from e
    if not isinstance(payload, dict):
        raise SystemExit("Invalid autonomous state format: expected JSON object")
    return payload


def _write_state(ws: Workspace, state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    ws.write_text(AUTONOMOUS_STATE_FILE, json_dumps(state))


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
        "updated_at": _utc_now(),
        "policy": asdict(policy),
        "attempts": gate_attempts,
    }
    ws.write_text(AUTONOMOUS_GATE_RESULTS_JSON, json_dumps(payload))


def _render_report(state: dict[str, Any], *, ok: bool, last_validation: Any) -> tuple[dict[str, Any], str]:
    attempts = state.get("attempts") if isinstance(state.get("attempts"), list) else []
    gate_attempts = [a for a in attempts if isinstance(a, dict) and isinstance(a.get("gate_results"), dict)]
    latest_gate_results = gate_attempts[-1].get("gate_results") if gate_attempts else None
    latest_strategy = _latest_strategy_from_attempts(attempts)
    report = {
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
        "gate_results": latest_gate_results,
        "latest_strategy": latest_strategy,
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
        "",
        "## Attempts",
    ]
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
        md.append(
            f"- Iteration {item.get('iteration')}: "
            f"`{'OK' if item.get('ok') else 'FAILED'}` "
            f"(resume={item.get('resume')}, reason={item.get('reason', '-')}{strategy_text}{gate_text})"
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
    start.add_argument("--workspace-allowlist", action="append", default=None)
    start.add_argument("--blocked-paths", action="append", default=None)
    start.add_argument("--allow-docker-build", action="store_true", default=None)
    start.add_argument("--allow-external-side-effects", action="store_true", default=None)

    status = sub.add_parser("status", help="print autonomous state for a run")
    status.add_argument("--run-dir", required=True)

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
    quality_gate_policy = _resolve_autonomous_quality_gate_policy(run_cfg)

    run_out = str(Path(args.run_dir).expanduser().resolve()) if args.run_dir else _resolve_output_dir(args.prd, args.out)
    ws = Workspace(run_out)

    if args.resume_state:
        loaded = _load_state(ws)
        if loaded is None:
            raise SystemExit(f"--resume-state requested, but state file missing: {run_out}/{AUTONOMOUS_STATE_FILE}")
        state = loaded
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
            quality_gate_policy=quality_gate_policy,
            prd_path=args.prd,
            config_path=args.config,
        )

    _enforce_path_boundaries(policy, prd=args.prd, config=args.config, out_root=args.out, run_out=run_out)

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
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))
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
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "time_budget_exceeded"
            _write_state(ws, state)
            break

        attempt_index += 1
        state["current_iteration"] = attempt_index
        state["phase"] = "plan"
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

        _write_state(ws, state)

        if ok:
            state["status"] = "completed"
            state["phase"] = "completed"
            _write_state(ws, state)
            break

        if attempt_index >= policy.max_iterations:
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "max_iterations_exceeded"
            _write_state(ws, state)
            break

        state["phase"] = "auto_fix_retry"
        _write_state(ws, state)

    llm_usage = client.usage_summary()
    run_metadata["llm_usage"] = llm_usage
    run_metadata["result_ok"] = bool(ok)
    run_metadata["run_completed_at"] = _utc_now()
    run_metadata["autonomous_latest_strategy"] = state.get("last_strategy")
    run_metadata["autonomous_strategy_trace_path"] = AUTONOMOUS_STRATEGY_TRACE_JSON
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    report_json, report_md = _render_report(state, ok=ok, last_validation=last_validation)
    ws.write_text(AUTONOMOUS_REPORT_JSON, json_dumps(report_json))
    ws.write_text(AUTONOMOUS_REPORT_MD, report_md)

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
        raise SystemExit("Usage: autodev autonomous <start|status> ...")
    action = argv[0]
    if action == "start":
        _start(argv[1:])
        return
    if action == "status":
        _status(argv[1:])
        return
    raise SystemExit(f"Unknown autonomous subcommand: {action}")
