"""Performance baseline tracking and regression detection.

Collects comprehensive run metrics after each autodev run, maintains a rolling
window of recent baselines in ``.autodev/perf_baseline.json``, and detects
regressions by comparing the current run against the baseline average.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autodev")

# ---------------------------------------------------------------------------
# Schema version — bump on breaking changes to perf_baseline.json format
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 2
DEFAULT_ROLLING_WINDOW = 5

# Tracked metrics and their default regression thresholds.
# max_ratio: fractional increase over baseline average (e.g. 0.50 = 50%).
# max_abs_ms: absolute increase in the metric value.
# None means "no threshold" (always passes).
DEFAULT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "total_elapsed_ms": {"max_ratio": 0.50, "max_abs_ms": 30_000, "direction": "lower_is_better"},
    "total_validation_ms": {"max_ratio": 0.50, "max_abs_ms": 30_000, "direction": "lower_is_better"},
    "total_llm_tokens": {"max_ratio": 0.50, "max_abs_ms": None, "direction": "lower_is_better"},
    "max_task_ms": {"max_ratio": 0.50, "max_abs_ms": 30_000, "direction": "lower_is_better"},
    # Quality metrics — higher is better; decline = regression
    "composite_score_avg": {"max_ratio": 0.15, "max_abs_ms": None, "direction": "higher_is_better"},
    "composite_score_min": {"max_ratio": 0.20, "max_abs_ms": None, "direction": "higher_is_better"},
}

_TRACKED_METRICS = list(DEFAULT_THRESHOLDS.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object) -> int:
    """Coerce to int, returning 0 on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunMetricsSnapshot:
    """Immutable metrics snapshot for a single run."""

    run_id: str
    timestamp: str  # ISO-8601
    profile: Optional[str]

    # Wall clock
    total_elapsed_ms: int

    # Phase durations (keyed by phase name)
    phase_durations_ms: Dict[str, int]

    # LLM metrics (aggregated across all roles)
    total_llm_prompt_tokens: int
    total_llm_completion_tokens: int
    total_llm_tokens: int
    total_llm_calls: int
    total_llm_retries: int

    # Task validation metrics
    total_validation_ms: int
    max_task_ms: int
    p95_task_ms: int
    median_task_ms: int
    task_count: int
    passed_tasks: int
    failed_tasks: int

    # Fix loop metrics
    total_task_attempts: int
    repair_passes: int

    # Per-task timing records for intelligent scheduling
    task_timings: List[Dict[str, Any]] = field(default_factory=list)

    # Per-validator pass/fail/duration records for adaptive quality gate
    validator_stats: List[Dict[str, Any]] = field(default_factory=list)

    # Quality score metrics (cross-run trending)
    composite_score_avg: float = 0.0     # avg composite across all tasks
    composite_score_min: float = 0.0     # worst task composite
    tests_score_avg: float = 0.0
    lint_score_avg: float = 0.0
    type_health_score_avg: float = 0.0
    security_score_avg: float = 0.0
    hard_blocked_tasks: int = 0
    reverted_attempts: int = 0
    accepted_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricVerdict:
    """Regression check result for a single metric."""

    metric_name: str
    current_value: float
    baseline_avg: float
    delta: float
    ratio: float  # delta / baseline_avg, 0.0 if baseline_avg == 0
    threshold_ratio: Optional[float]
    threshold_abs: Optional[float]
    ratio_ok: bool
    abs_ok: bool
    ok: bool  # ratio_ok AND abs_ok


@dataclass
class PerfBaselineResult:
    """Overall regression detection result."""

    has_baseline: bool
    baseline_run_count: int
    ok: bool  # all metric verdicts pass
    verdicts: List[MetricVerdict] = field(default_factory=list)
    current_snapshot: Optional[RunMetricsSnapshot] = None
    baseline_avg: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_baseline": self.has_baseline,
            "baseline_run_count": self.baseline_run_count,
            "ok": self.ok,
            "verdicts": [
                {
                    "metric_name": v.metric_name,
                    "current_value": v.current_value,
                    "baseline_avg": v.baseline_avg,
                    "delta": v.delta,
                    "ratio": round(v.ratio, 4),
                    "threshold_ratio": v.threshold_ratio,
                    "threshold_abs": v.threshold_abs,
                    "ratio_ok": v.ratio_ok,
                    "abs_ok": v.abs_ok,
                    "ok": v.ok,
                }
                for v in self.verdicts
            ],
            "baseline_avg": self.baseline_avg,
        }


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def _compute_task_metrics(quality_summary: Dict[str, Any]) -> Dict[str, int]:
    """Extract task-level duration metrics from quality_summary structure.

    Mirrors the logic in ``docs/ops/perf_validation.py`` ``_collect_from_task_index()``
    but operates on the in-memory dict that ``loop.py`` has already assembled.
    """
    tasks = quality_summary.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        return {
            "total_validation_ms": 0,
            "max_task_ms": 0,
            "p95_task_ms": 0,
            "median_task_ms": 0,
            "task_count": 0,
            "passed_tasks": 0,
            "failed_tasks": 0,
        }

    durations: List[int] = []
    passed = 0
    failed = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status", "unknown"))
        if status == "passed":
            passed += 1
        else:
            failed += 1

        attempt_trend = task.get("attempt_trend", [])
        if isinstance(attempt_trend, list) and attempt_trend:
            last = attempt_trend[-1]
            if isinstance(last, dict):
                durations.append(_safe_int(last.get("duration_ms", 0)))
            else:
                durations.append(0)
        else:
            durations.append(0)

    if not durations:
        durations = [0]

    sorted_durations = sorted(durations)
    p95_idx = max(0, int((len(sorted_durations) - 1) * 0.95))

    return {
        "total_validation_ms": sum(durations),
        "max_task_ms": max(durations),
        "p95_task_ms": sorted_durations[p95_idx],
        "median_task_ms": int(median(durations)),
        "task_count": len(durations),
        "passed_tasks": passed,
        "failed_tasks": failed,
    }


def collect_run_metrics(
    run_id: str,
    profile: Optional[str],
    trace_dict: Dict[str, Any],
    quality_summary: Dict[str, Any],
    task_timings: List[Dict[str, Any]] | None = None,
    validator_stats: List[Dict[str, Any]] | None = None,
) -> RunMetricsSnapshot:
    """Build a :class:`RunMetricsSnapshot` from run_trace and quality_summary."""

    # Wall clock from trace
    total_elapsed_ms = _safe_int(trace_dict.get("total_elapsed_ms", 0))

    # Phase durations
    phase_durations_ms: Dict[str, int] = {}
    for p in trace_dict.get("phases", []):
        if isinstance(p, dict):
            phase_durations_ms[str(p.get("phase", ""))] = _safe_int(
                p.get("duration_ms", 0)
            )

    # LLM metrics — sum across all roles
    llm_metrics = trace_dict.get("llm_metrics", {})
    total_prompt = 0
    total_completion = 0
    total_calls = 0
    total_retries = 0
    if isinstance(llm_metrics, dict):
        for m in llm_metrics.values():
            if not isinstance(m, dict):
                continue
            total_prompt += _safe_int(m.get("total_prompt_tokens", 0))
            total_completion += _safe_int(m.get("total_completion_tokens", 0))
            total_calls += _safe_int(m.get("call_count", 0))
            total_retries += _safe_int(m.get("retry_count", 0))

    # Task validation metrics
    task_metrics = _compute_task_metrics(quality_summary)

    # Fix loop metrics from totals
    totals = quality_summary.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}

    # Quality score metrics from experiment log summary
    exp_log = quality_summary.get("experiment_log", {})
    if not isinstance(exp_log, dict):
        exp_log = {}
    exp_tasks = exp_log.get("tasks", {})
    if not isinstance(exp_tasks, dict):
        exp_tasks = {}

    composite_score_avg = 0.0
    composite_score_min = 0.0
    hard_blocked_tasks = 0
    reverted_attempts = 0
    accepted_attempts = 0

    if exp_tasks:
        final_scores = [float(t.get("final_score", 0)) for t in exp_tasks.values() if isinstance(t, dict)]
        if final_scores:
            composite_score_avg = sum(final_scores) / len(final_scores)
            composite_score_min = min(final_scores)

        for t_info in exp_tasks.values():
            if not isinstance(t_info, dict):
                continue
            decisions = t_info.get("decisions", {})
            if isinstance(decisions, dict):
                reverted_attempts += _safe_int(decisions.get("reverted", 0))
                accepted_attempts += _safe_int(decisions.get("accepted", 0))

    return RunMetricsSnapshot(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        profile=profile,
        total_elapsed_ms=total_elapsed_ms,
        phase_durations_ms=phase_durations_ms,
        total_llm_prompt_tokens=total_prompt,
        total_llm_completion_tokens=total_completion,
        total_llm_tokens=total_prompt + total_completion,
        total_llm_calls=total_calls,
        total_llm_retries=total_retries,
        total_validation_ms=task_metrics["total_validation_ms"],
        max_task_ms=task_metrics["max_task_ms"],
        p95_task_ms=task_metrics["p95_task_ms"],
        median_task_ms=task_metrics["median_task_ms"],
        task_count=task_metrics["task_count"],
        passed_tasks=task_metrics["passed_tasks"],
        failed_tasks=task_metrics["failed_tasks"],
        total_task_attempts=_safe_int(totals.get("total_task_attempts", 0)),
        repair_passes=_safe_int(totals.get("repair_passes", 0)),
        task_timings=task_timings or [],
        validator_stats=validator_stats or [],
        composite_score_avg=composite_score_avg,
        composite_score_min=composite_score_min,
        hard_blocked_tasks=hard_blocked_tasks,
        reverted_attempts=reverted_attempts,
        accepted_attempts=accepted_attempts,
    )


# ---------------------------------------------------------------------------
# Baseline persistence
# ---------------------------------------------------------------------------


def _empty_baseline() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "runs": []}


def _read_baseline(baseline_path: str) -> Dict[str, Any]:
    """Load perf_baseline.json, returning empty structure on missing/corrupt."""
    if not os.path.exists(baseline_path):
        return _empty_baseline()
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("perf_baseline: corrupted baseline file, resetting")
        return _empty_baseline()
    if not isinstance(data, dict):
        return _empty_baseline()
    if _safe_int(data.get("schema_version")) != SCHEMA_VERSION:
        logger.warning("perf_baseline: schema_version mismatch, resetting")
        return _empty_baseline()
    return data


def _write_baseline(baseline_path: str, payload: Dict[str, Any]) -> None:
    """Write baseline JSON to disk."""
    parent = os.path.dirname(baseline_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)


def _append_snapshot(
    baseline: Dict[str, Any],
    snapshot: RunMetricsSnapshot,
    window_size: int = DEFAULT_ROLLING_WINDOW,
) -> Dict[str, Any]:
    """Append snapshot to baseline, trimming to rolling window.

    Returns a **new** dict (not mutated in-place).
    """
    runs = list(baseline.get("runs", []))
    runs.append(snapshot.to_dict())
    # Keep only the most recent `window_size` runs
    if len(runs) > window_size:
        runs = runs[-window_size:]
    result = dict(baseline)
    result["runs"] = runs
    return result


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


def _compute_baseline_averages(
    runs: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Compute average values for tracked metrics across baseline runs."""
    if not runs:
        return {k: 0.0 for k in _TRACKED_METRICS}

    sums: Dict[str, float] = {k: 0.0 for k in _TRACKED_METRICS}
    for run in runs:
        if not isinstance(run, dict):
            continue
        for metric in _TRACKED_METRICS:
            try:
                sums[metric] += float(run.get(metric, 0))
            except (TypeError, ValueError):
                pass

    count = len(runs)
    return {k: v / count for k, v in sums.items()}


def _resolve_thresholds(
    quality_profile: Dict[str, Any] | None,
) -> Dict[str, Dict[str, Any]]:
    """Merge user thresholds from quality_profile into defaults."""
    result = {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()}

    if not isinstance(quality_profile, dict):
        return result

    perf_cfg = quality_profile.get("perf_baseline")
    if not isinstance(perf_cfg, dict):
        return result

    user_thresholds = perf_cfg.get("thresholds")
    if not isinstance(user_thresholds, dict):
        return result

    for metric, overrides in user_thresholds.items():
        if metric not in result or not isinstance(overrides, dict):
            continue
        for key in ("max_ratio", "max_abs_ms"):
            if key in overrides:
                result[metric][key] = overrides[key]

    return result


def _check_metric(
    metric_name: str,
    current_value: float,
    baseline_avg: float,
    thresholds: Dict[str, Any],
) -> MetricVerdict:
    """Compare a single metric against its thresholds.

    Supports ``direction`` in thresholds:
    - ``"lower_is_better"`` (default): increase = regression.
    - ``"higher_is_better"``: decrease = regression.
    """
    direction = thresholds.get("direction", "lower_is_better")

    if direction == "higher_is_better":
        # For quality scores: a drop is bad.  delta = baseline_avg - current
        delta = baseline_avg - current_value
    else:
        # For perf metrics: an increase is bad.  delta = current - baseline_avg
        delta = current_value - baseline_avg

    ratio = (delta / baseline_avg) if baseline_avg > 0 else 0.0

    max_ratio = thresholds.get("max_ratio")
    max_abs = thresholds.get("max_abs_ms")

    ratio_ok = True
    if max_ratio is not None and baseline_avg > 0:
        ratio_ok = ratio <= max_ratio

    abs_ok = True
    if max_abs is not None:
        abs_ok = delta <= max_abs

    return MetricVerdict(
        metric_name=metric_name,
        current_value=current_value,
        baseline_avg=baseline_avg,
        delta=delta,
        ratio=round(ratio, 6),
        threshold_ratio=max_ratio,
        threshold_abs=max_abs,
        ratio_ok=ratio_ok,
        abs_ok=abs_ok,
        ok=ratio_ok and abs_ok,
    )


def detect_regression(
    snapshot: RunMetricsSnapshot,
    baseline: Dict[str, Any],
    quality_profile: Dict[str, Any] | None = None,
) -> PerfBaselineResult:
    """Compare current run against baseline average.

    Returns ``PerfBaselineResult`` with ``has_baseline=False`` and ``ok=True``
    when no prior runs exist.
    """
    runs = baseline.get("runs", [])
    if not isinstance(runs, list) or not runs:
        return PerfBaselineResult(
            has_baseline=False,
            baseline_run_count=0,
            ok=True,
            verdicts=[],
            current_snapshot=snapshot,
            baseline_avg={},
        )

    averages = _compute_baseline_averages(runs)
    thresholds = _resolve_thresholds(quality_profile)

    snapshot_dict = snapshot.to_dict()
    verdicts: List[MetricVerdict] = []
    for metric in _TRACKED_METRICS:
        try:
            current = float(snapshot_dict.get(metric, 0))
        except (TypeError, ValueError):
            current = 0.0
        avg = averages.get(metric, 0.0)
        metric_thresholds = thresholds.get(metric, {})
        verdicts.append(_check_metric(metric, current, avg, metric_thresholds))

    all_ok = all(v.ok for v in verdicts)

    return PerfBaselineResult(
        has_baseline=True,
        baseline_run_count=len(runs),
        ok=all_ok,
        verdicts=verdicts,
        current_snapshot=snapshot,
        baseline_avg=averages,
    )


# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------


def record_and_check(
    ws_root: str,
    run_id: str,
    profile: Optional[str],
    trace_dict: Dict[str, Any],
    quality_summary: Dict[str, Any],
    quality_profile: Dict[str, Any] | None = None,
    task_timings: List[Dict[str, Any]] | None = None,
    validator_stats: List[Dict[str, Any]] | None = None,
) -> PerfBaselineResult:
    """End-of-run entry point: collect, persist baseline, detect regressions.

    Steps:
    1. Check if perf baseline is enabled (default: True)
    2. ``collect_run_metrics()`` → ``RunMetricsSnapshot``
    3. ``_read_baseline()`` from ``.autodev/perf_baseline.json``
    4. ``detect_regression()`` against existing baseline
    5. ``_append_snapshot()`` to baseline (after comparison)
    6. ``_write_baseline()`` with ``last_check_result``
    7. Return ``PerfBaselineResult``
    """
    # Check enabled flag
    if isinstance(quality_profile, dict):
        perf_cfg = quality_profile.get("perf_baseline")
        if isinstance(perf_cfg, dict) and perf_cfg.get("enabled") is False:
            snapshot = collect_run_metrics(
                run_id, profile, trace_dict, quality_summary,
                task_timings=task_timings,
                validator_stats=validator_stats,
            )
            return PerfBaselineResult(
                has_baseline=False,
                baseline_run_count=0,
                ok=True,
                current_snapshot=snapshot,
            )

    # Resolve rolling window size
    window_size = DEFAULT_ROLLING_WINDOW
    if isinstance(quality_profile, dict):
        perf_cfg = quality_profile.get("perf_baseline")
        if isinstance(perf_cfg, dict):
            user_window = perf_cfg.get("rolling_window")
            if isinstance(user_window, int) and user_window > 0:
                window_size = user_window

    snapshot = collect_run_metrics(
        run_id, profile, trace_dict, quality_summary,
        task_timings=task_timings,
        validator_stats=validator_stats,
    )

    baseline_path = os.path.join(ws_root, ".autodev", "perf_baseline.json")
    baseline = _read_baseline(baseline_path)

    # Detect regression BEFORE appending current run
    result = detect_regression(snapshot, baseline, quality_profile)

    # Append current snapshot to baseline (rolling window)
    updated = _append_snapshot(baseline, snapshot, window_size=window_size)
    updated["last_check_result"] = result.to_dict()

    _write_baseline(baseline_path, updated)

    return result
