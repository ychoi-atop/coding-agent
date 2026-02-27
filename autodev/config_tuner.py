"""Self-healing config tuner — data-driven quality_profile recommendations.

Analyses the previous run's :class:`~run_analyzer.RunAnalysis` to generate
parameter adjustment recommendations.  **Pure read-only** — never writes
config files; recommendations are advisory only.

Tuning rules:

* Chronically failing validators → add to ``soft_fail``
* Low repair resolution rate → increase ``max_retries``
* High repair loop count → enable ``parallel_fixer``
* Cross-run pass-rate trends → adjust ``consecutive_pass_threshold``
* Many always-passing validators → enable ``smart_scope``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

from .run_analyzer import RunAnalysis


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Confidence(str, Enum):
    """Confidence level for a recommendation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TuningCategory(str, Enum):
    """Grouping category for recommendations."""

    SOFT_FAIL = "soft_fail"
    ESCALATION = "escalation"
    ADAPTIVE_GATE = "adaptive_gate"
    PARALLEL_FIXER = "parallel_fixer"
    SMART_SCOPE = "smart_scope"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """A single parameter adjustment recommendation."""

    parameter_path: str
    current_value: Any
    recommended_value: Any
    reason: str
    confidence: Confidence
    category: TuningCategory


@dataclass
class TunerResult:
    """Complete set of recommendations from a tuning analysis."""

    recommendations: List[Recommendation] = field(default_factory=list)
    analysis_run_id: str = ""
    trend_run_count: int = 0

    @property
    def by_category(self) -> Dict[TuningCategory, List[Recommendation]]:
        """Group recommendations by category."""
        groups: Dict[TuningCategory, List[Recommendation]] = {}
        for rec in self.recommendations:
            groups.setdefault(rec.category, []).append(rec)
        return groups

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for JSON output."""
        return {
            "analysis_run_id": self.analysis_run_id,
            "trend_run_count": self.trend_run_count,
            "recommendation_count": len(self.recommendations),
            "recommendations": [
                {
                    "parameter_path": r.parameter_path,
                    "current_value": r.current_value,
                    "recommended_value": r.recommended_value,
                    "reason": r.reason,
                    "confidence": r.confidence.value,
                    "category": r.category.value,
                }
                for r in self.recommendations
            ],
        }


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------

_LOW_PASS_RATE_THRESHOLD = 50.0
_VERY_LOW_PASS_RATE = 25.0
_PERFECT_PASS_RATE = 100.0
_MIN_CALLS_FOR_CONFIDENCE = 5
_LOW_RESOLUTION_THRESHOLD = 50.0
_MAX_RETRIES_CAP = 3
_HIGH_REPAIR_LOOPS_THRESHOLD = 4
_MIN_TREND_POINTS = 3
_TREND_DELTA_THRESHOLD = 10.0
_MIN_ALWAYS_PASS_FOR_SMART_SCOPE = 3
_MIN_VALIDATORS_FOR_SMART_SCOPE = 4


# ---------------------------------------------------------------------------
# Config extraction helpers
# ---------------------------------------------------------------------------


def _extract_soft_fail_list(
    quality_profile: Dict[str, Any], section: str,
) -> List[str]:
    """Extract current soft_fail list from validator_policy.{section}."""
    vp = quality_profile.get("validator_policy")
    if not isinstance(vp, dict):
        return []
    sec = vp.get(section)
    if not isinstance(sec, dict):
        return []
    sf = sec.get("soft_fail")
    if isinstance(sf, list):
        return [str(v) for v in sf]
    return []


def _extract_adaptive_gate_mode(quality_profile: Dict[str, Any]) -> str:
    ag = quality_profile.get("adaptive_gate")
    if isinstance(ag, dict):
        return str(ag.get("mode", "balanced"))
    return "balanced"


def _extract_consecutive_pass_threshold(quality_profile: Dict[str, Any]) -> int:
    ag = quality_profile.get("adaptive_gate")
    if isinstance(ag, dict):
        val = _safe_int(ag.get("consecutive_pass_threshold", 5))
        return val if val > 0 else 5
    return 5


def _extract_max_retries(quality_profile: Dict[str, Any]) -> int:
    esc = quality_profile.get("escalation")
    if isinstance(esc, dict):
        guard = esc.get("repeat_failure_guard")
        if isinstance(guard, dict):
            val = _safe_int(guard.get("max_retries_before_targeted_fix", 1))
            return val if val > 0 else 1
    return 1


def _extract_parallel_fixer_enabled(quality_profile: Dict[str, Any]) -> bool:
    pf = quality_profile.get("parallel_fixer")
    if isinstance(pf, dict):
        return pf.get("enabled", False) is True
    return False


def _extract_smart_scope_enabled(quality_profile: Dict[str, Any]) -> bool:
    ss = quality_profile.get("smart_scope")
    if isinstance(ss, dict):
        return ss.get("enabled", False) is True
    return False


# ---------------------------------------------------------------------------
# Tuning rules
# ---------------------------------------------------------------------------


def _recommend_soft_fail(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any],
    recs: List[Recommendation],
) -> None:
    """Low pass-rate → soft_fail; perfect pass-rate → aggressive mode."""
    current_per_task_soft = _extract_soft_fail_list(quality_profile, "per_task")

    for v in analysis.validators:
        # Chronically failing → add to soft_fail
        if v.pass_rate < _LOW_PASS_RATE_THRESHOLD and v.call_count >= 2:
            if v.name not in current_per_task_soft:
                conf = (
                    Confidence.HIGH
                    if v.pass_rate < _VERY_LOW_PASS_RATE
                    else Confidence.MEDIUM
                )
                recs.append(Recommendation(
                    parameter_path="validator_policy.per_task.soft_fail",
                    current_value=current_per_task_soft,
                    recommended_value=sorted(set(current_per_task_soft) | {v.name}),
                    reason=(
                        f"Validator '{v.name}' has pass_rate={v.pass_rate}% "
                        f"across {v.call_count} calls; soft-fail prevents blocking"
                    ),
                    confidence=conf,
                    category=TuningCategory.SOFT_FAIL,
                ))

        # Always passing with enough data → suggest aggressive mode
        if (
            v.pass_rate == _PERFECT_PASS_RATE
            and v.call_count >= _MIN_CALLS_FOR_CONFIDENCE
        ):
            current_mode = _extract_adaptive_gate_mode(quality_profile)
            if current_mode != "aggressive":
                recs.append(Recommendation(
                    parameter_path="adaptive_gate.mode",
                    current_value=current_mode,
                    recommended_value="aggressive",
                    reason=(
                        f"Validator '{v.name}' has 100% pass rate across "
                        f"{v.call_count} calls; aggressive mode safe"
                    ),
                    confidence=Confidence.MEDIUM,
                    category=TuningCategory.ADAPTIVE_GATE,
                ))


def _recommend_escalation(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any],
    recs: List[Recommendation],
) -> None:
    """Low resolution rate → increase max_retries."""
    current_max_retries = _extract_max_retries(quality_profile)

    for cat in analysis.repair_categories:
        if (
            cat.resolution_rate < _LOW_RESOLUTION_THRESHOLD
            and cat.occurrences >= 2
        ):
            suggested = min(current_max_retries + 1, _MAX_RETRIES_CAP)
            if suggested > current_max_retries:
                conf = (
                    Confidence.MEDIUM
                    if cat.occurrences >= 3
                    else Confidence.LOW
                )
                recs.append(Recommendation(
                    parameter_path="escalation.repeat_failure_guard.max_retries_before_targeted_fix",
                    current_value=current_max_retries,
                    recommended_value=suggested,
                    reason=(
                        f"Category '{cat.category}' has resolution_rate="
                        f"{cat.resolution_rate}% ({cat.resolved_count}/"
                        f"{cat.occurrences}); more retries may help"
                    ),
                    confidence=conf,
                    category=TuningCategory.ESCALATION,
                ))


def _recommend_parallel_fixer(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any],
    recs: List[Recommendation],
) -> None:
    """High repair loops → enable parallel_fixer."""
    current_enabled = _extract_parallel_fixer_enabled(quality_profile)

    if (
        analysis.total_repair_loops >= _HIGH_REPAIR_LOOPS_THRESHOLD
        and not current_enabled
    ):
        recs.append(Recommendation(
            parameter_path="parallel_fixer.enabled",
            current_value=False,
            recommended_value=True,
            reason=(
                f"Run had {analysis.total_repair_loops} repair loops; "
                f"parallel fixer can reduce wall clock time"
            ),
            confidence=Confidence.MEDIUM,
            category=TuningCategory.PARALLEL_FIXER,
        ))


def _recommend_from_trends(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any],
    recs: List[Recommendation],
) -> None:
    """Pass-rate trend ±10% → adjust consecutive_pass_threshold."""
    trends = analysis.trends
    if len(trends) < _MIN_TREND_POINTS:
        return

    mid = len(trends) // 2
    first_half = trends[:mid]
    second_half = trends[mid:]

    avg_first = sum(t.pass_rate for t in first_half) / len(first_half)
    avg_second = sum(t.pass_rate for t in second_half) / len(second_half)
    delta = avg_second - avg_first

    current_threshold = _extract_consecutive_pass_threshold(quality_profile)

    if delta > _TREND_DELTA_THRESHOLD:
        # Improving → tighten
        suggested = max(current_threshold - 1, 2)
        if suggested < current_threshold:
            recs.append(Recommendation(
                parameter_path="adaptive_gate.consecutive_pass_threshold",
                current_value=current_threshold,
                recommended_value=suggested,
                reason=(
                    f"Pass rate trending up (+{delta:.1f}% over "
                    f"{len(trends)} runs); safe to tighten threshold"
                ),
                confidence=Confidence.LOW,
                category=TuningCategory.ADAPTIVE_GATE,
            ))
    elif delta < -_TREND_DELTA_THRESHOLD:
        # Regressing → relax
        suggested = min(current_threshold + 2, 10)
        if suggested > current_threshold:
            recs.append(Recommendation(
                parameter_path="adaptive_gate.consecutive_pass_threshold",
                current_value=current_threshold,
                recommended_value=suggested,
                reason=(
                    f"Pass rate trending down ({delta:.1f}% over "
                    f"{len(trends)} runs); relaxing threshold for stability"
                ),
                confidence=Confidence.LOW,
                category=TuningCategory.ADAPTIVE_GATE,
            ))


def _recommend_smart_scope(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any],
    recs: List[Recommendation],
) -> None:
    """Many always-passing validators → enable smart_scope."""
    if _extract_smart_scope_enabled(quality_profile):
        return

    always_pass = [
        v
        for v in analysis.validators
        if v.pass_rate == _PERFECT_PASS_RATE and v.call_count >= 3
    ]
    if (
        len(always_pass) >= _MIN_ALWAYS_PASS_FOR_SMART_SCOPE
        and len(analysis.validators) >= _MIN_VALIDATORS_FOR_SMART_SCOPE
    ):
        recs.append(Recommendation(
            parameter_path="smart_scope.enabled",
            current_value=False,
            recommended_value=True,
            reason=(
                f"{len(always_pass)} of {len(analysis.validators)} validators "
                f"always pass; smart scope can skip irrelevant ones"
            ),
            confidence=Confidence.LOW,
            category=TuningCategory.SMART_SCOPE,
        ))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_recommendations(
    analysis: RunAnalysis,
    quality_profile: Dict[str, Any] | None = None,
) -> TunerResult:
    """Generate config tuning recommendations from run analysis data.

    Pure read-only function.  Returns recommendations but **never** writes
    config files.  Gracefully returns empty result on ``None`` / empty input.
    """
    qp = quality_profile if isinstance(quality_profile, dict) else {}
    recs: List[Recommendation] = []

    _recommend_soft_fail(analysis, qp, recs)
    _recommend_escalation(analysis, qp, recs)
    _recommend_parallel_fixer(analysis, qp, recs)
    _recommend_from_trends(analysis, qp, recs)
    _recommend_smart_scope(analysis, qp, recs)

    return TunerResult(
        recommendations=recs,
        analysis_run_id=analysis.run_id,
        trend_run_count=len(analysis.trends),
    )


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

_SEP = "=" * 80
_LINE = "-" * 80


def format_recommendations(result: TunerResult) -> str:
    """Render a :class:`TunerResult` as a human-readable ASCII report."""
    lines: List[str] = []

    lines.append(_SEP)
    lines.append(f"CONFIG TUNER RECOMMENDATIONS (run: {result.analysis_run_id})")
    lines.append(
        f"Total: {len(result.recommendations)} recommendation(s) | "
        f"Trend data: {result.trend_run_count} run(s)"
    )
    lines.append(_SEP)

    if not result.recommendations:
        lines.append("")
        lines.append("No recommendations — current config looks appropriate.")
        lines.append(_SEP)
        return "\n".join(lines)

    for category, cat_recs in sorted(
        result.by_category.items(), key=lambda x: x[0].value,
    ):
        lines.append("")
        lines.append(f"[{category.value.upper()}]")
        lines.append(_LINE)
        for rec in cat_recs:
            lines.append(f"  Parameter:  {rec.parameter_path}")
            lines.append(f"  Current:    {rec.current_value}")
            lines.append(f"  Suggested:  {rec.recommended_value}")
            lines.append(f"  Reason:     {rec.reason}")
            lines.append(f"  Confidence: {rec.confidence.value}")
            lines.append("")

    lines.append(_SEP)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object) -> int:
    """Coerce to int, returning 0 on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
