"""Tests for autodev.config_tuner module."""

from __future__ import annotations

from autodev.config_tuner import (
    Confidence,
    TuningCategory,
    generate_recommendations,
    format_recommendations,
    _recommend_escalation,
    _recommend_from_trends,
    _recommend_parallel_fixer,
    _recommend_smart_scope,
    _recommend_soft_fail,
)
from autodev.run_analyzer import (
    RepairCategoryStats,
    RunAnalysis,
    RunTrendPoint,
    ValidatorProfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_validator(
    name: str = "ruff",
    pass_rate: float = 80.0,
    call_count: int = 5,
    fail_count: int = 1,
) -> ValidatorProfile:
    pass_count = call_count - fail_count
    return ValidatorProfile(
        name=name,
        total_duration_ms=1000,
        call_count=call_count,
        pass_count=pass_count,
        fail_count=fail_count,
        pass_rate=pass_rate,
        avg_duration_ms=200,
    )


def _make_repair_cat(
    category: str = "lint_error",
    occurrences: int = 3,
    resolved_count: int = 1,
    resolution_rate: float = 33.3,
    max_escalation_level: int = 2,
) -> RepairCategoryStats:
    return RepairCategoryStats(
        category=category,
        occurrences=occurrences,
        resolved_count=resolved_count,
        resolution_rate=resolution_rate,
        max_escalation_level=max_escalation_level,
    )


def _make_trend(
    run_id: str = "run-1",
    pass_rate: float = 80.0,
) -> RunTrendPoint:
    return RunTrendPoint(
        run_id=run_id,
        timestamp="2025-01-01T00:00:00",
        total_elapsed_ms=50000,
        total_llm_tokens=50000,
        task_count=5,
        passed_tasks=int(5 * pass_rate / 100),
        pass_rate=pass_rate,
    )


def _make_analysis(
    validators: list | None = None,
    repair_categories: list | None = None,
    trends: list | None = None,
    total_repair_loops: int = 0,
    overall_pass_rate: float = 80.0,
) -> RunAnalysis:
    return RunAnalysis(
        run_id="test-run-1",
        total_elapsed_ms=50000,
        validators=validators or [],
        repair_categories=repair_categories or [],
        trends=trends or [],
        total_repair_loops=total_repair_loops,
        overall_pass_rate=overall_pass_rate,
    )


# ---------------------------------------------------------------------------
# Test 1-3: Soft-fail recommendations
# ---------------------------------------------------------------------------


def test_low_pass_rate_recommends_soft_fail():
    """pass_rate=20% → soft_fail recommendation, HIGH confidence."""
    analysis = _make_analysis(validators=[
        _make_validator("bandit", pass_rate=20.0, call_count=5, fail_count=4),
    ])
    recs: list = []
    _recommend_soft_fail(analysis, {}, recs)

    assert len(recs) >= 1
    sf_recs = [r for r in recs if r.category == TuningCategory.SOFT_FAIL]
    assert len(sf_recs) == 1
    assert sf_recs[0].confidence == Confidence.HIGH  # < 25% → HIGH
    assert "bandit" in sf_recs[0].recommended_value


def test_medium_pass_rate_recommends_soft_fail():
    """pass_rate=45% → soft_fail recommendation, MEDIUM confidence."""
    analysis = _make_analysis(validators=[
        _make_validator("mypy", pass_rate=45.0, call_count=4, fail_count=2),
    ])
    recs: list = []
    _recommend_soft_fail(analysis, {}, recs)

    sf_recs = [r for r in recs if r.category == TuningCategory.SOFT_FAIL]
    assert len(sf_recs) == 1
    assert sf_recs[0].confidence == Confidence.MEDIUM


def test_already_in_soft_fail_no_duplicate():
    """Validator already in soft_fail → no duplicate recommendation."""
    analysis = _make_analysis(validators=[
        _make_validator("bandit", pass_rate=20.0, call_count=5, fail_count=4),
    ])
    qp = {
        "validator_policy": {
            "per_task": {"soft_fail": ["bandit"]},
        },
    }
    recs: list = []
    _recommend_soft_fail(analysis, qp, recs)

    sf_recs = [r for r in recs if r.category == TuningCategory.SOFT_FAIL]
    assert len(sf_recs) == 0


# ---------------------------------------------------------------------------
# Test 4-5: Always-passing validator → aggressive mode
# ---------------------------------------------------------------------------


def test_perfect_validator_suggests_aggressive():
    """100% pass rate, 5+ calls → aggressive mode recommendation."""
    analysis = _make_analysis(validators=[
        _make_validator("ruff", pass_rate=100.0, call_count=8, fail_count=0),
    ])
    recs: list = []
    _recommend_soft_fail(analysis, {}, recs)

    ag_recs = [r for r in recs if r.category == TuningCategory.ADAPTIVE_GATE]
    assert len(ag_recs) == 1
    assert ag_recs[0].recommended_value == "aggressive"
    assert ag_recs[0].confidence == Confidence.MEDIUM


def test_perfect_validator_insufficient_calls():
    """100% pass but only 2 calls → no recommendation."""
    analysis = _make_analysis(validators=[
        _make_validator("ruff", pass_rate=100.0, call_count=2, fail_count=0),
    ])
    recs: list = []
    _recommend_soft_fail(analysis, {}, recs)

    ag_recs = [r for r in recs if r.category == TuningCategory.ADAPTIVE_GATE]
    assert len(ag_recs) == 0


# ---------------------------------------------------------------------------
# Test 6-8: Escalation recommendations
# ---------------------------------------------------------------------------


def test_low_resolution_increases_retries():
    """resolution_rate=33% → max_retries +1."""
    analysis = _make_analysis(repair_categories=[
        _make_repair_cat("lint_error", occurrences=3, resolved_count=1,
                         resolution_rate=33.3),
    ])
    recs: list = []
    _recommend_escalation(analysis, {}, recs)

    assert len(recs) == 1
    assert recs[0].category == TuningCategory.ESCALATION
    assert recs[0].current_value == 1
    assert recs[0].recommended_value == 2
    assert recs[0].confidence == Confidence.MEDIUM  # occurrences=3 ≥ 3


def test_resolution_above_threshold_no_rec():
    """resolution_rate=80% → no recommendation."""
    analysis = _make_analysis(repair_categories=[
        _make_repair_cat("lint_error", occurrences=5, resolved_count=4,
                         resolution_rate=80.0),
    ])
    recs: list = []
    _recommend_escalation(analysis, {}, recs)
    assert len(recs) == 0


def test_retries_capped_at_three():
    """Already at max_retries=3 → no recommendation."""
    analysis = _make_analysis(repair_categories=[
        _make_repair_cat("lint_error", occurrences=5, resolved_count=1,
                         resolution_rate=20.0),
    ])
    qp = {
        "escalation": {
            "repeat_failure_guard": {"max_retries_before_targeted_fix": 3},
        },
    }
    recs: list = []
    _recommend_escalation(analysis, qp, recs)
    assert len(recs) == 0


# ---------------------------------------------------------------------------
# Test 9-10: Parallel fixer recommendations
# ---------------------------------------------------------------------------


def test_high_repair_loops_enables_parallel():
    """6 repair loops → parallel_fixer.enabled=True."""
    analysis = _make_analysis(total_repair_loops=6)
    recs: list = []
    _recommend_parallel_fixer(analysis, {}, recs)

    assert len(recs) == 1
    assert recs[0].category == TuningCategory.PARALLEL_FIXER
    assert recs[0].recommended_value is True


def test_parallel_already_enabled_no_rec():
    """Already enabled → no recommendation."""
    analysis = _make_analysis(total_repair_loops=10)
    qp = {"parallel_fixer": {"enabled": True}}
    recs: list = []
    _recommend_parallel_fixer(analysis, qp, recs)
    assert len(recs) == 0


# ---------------------------------------------------------------------------
# Test 11-13: Trend-based recommendations
# ---------------------------------------------------------------------------


def test_improving_trend_tightens_threshold():
    """+15% trend → lower consecutive_pass_threshold."""
    analysis = _make_analysis(trends=[
        _make_trend("r1", pass_rate=60.0),
        _make_trend("r2", pass_rate=65.0),
        _make_trend("r3", pass_rate=80.0),
        _make_trend("r4", pass_rate=90.0),
    ])
    recs: list = []
    _recommend_from_trends(analysis, {}, recs)

    assert len(recs) == 1
    assert recs[0].parameter_path == "adaptive_gate.consecutive_pass_threshold"
    assert recs[0].recommended_value < recs[0].current_value


def test_regressing_trend_relaxes_threshold():
    """-15% trend → higher consecutive_pass_threshold."""
    analysis = _make_analysis(trends=[
        _make_trend("r1", pass_rate=95.0),
        _make_trend("r2", pass_rate=90.0),
        _make_trend("r3", pass_rate=70.0),
        _make_trend("r4", pass_rate=60.0),
    ])
    recs: list = []
    _recommend_from_trends(analysis, {}, recs)

    assert len(recs) == 1
    assert recs[0].recommended_value > recs[0].current_value


def test_insufficient_trend_data_no_rec():
    """Only 2 trend points → no recommendation."""
    analysis = _make_analysis(trends=[
        _make_trend("r1", pass_rate=60.0),
        _make_trend("r2", pass_rate=90.0),
    ])
    recs: list = []
    _recommend_from_trends(analysis, {}, recs)
    assert len(recs) == 0


# ---------------------------------------------------------------------------
# Test 14-15: Smart scope recommendations
# ---------------------------------------------------------------------------


def test_many_passing_suggests_smart_scope():
    """4 of 5 validators always pass → enable smart_scope."""
    analysis = _make_analysis(validators=[
        _make_validator("ruff", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("mypy", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("pytest", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("bandit", pass_rate=100.0, call_count=3, fail_count=0),
        _make_validator("docker_build", pass_rate=60.0, call_count=5, fail_count=2),
    ])
    recs: list = []
    _recommend_smart_scope(analysis, {}, recs)

    assert len(recs) == 1
    assert recs[0].category == TuningCategory.SMART_SCOPE
    assert recs[0].recommended_value is True


def test_smart_scope_already_enabled_no_rec():
    """Already enabled → no recommendation."""
    analysis = _make_analysis(validators=[
        _make_validator("ruff", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("mypy", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("pytest", pass_rate=100.0, call_count=5, fail_count=0),
        _make_validator("bandit", pass_rate=100.0, call_count=5, fail_count=0),
    ])
    qp = {"smart_scope": {"enabled": True}}
    recs: list = []
    _recommend_smart_scope(analysis, qp, recs)
    assert len(recs) == 0


# ---------------------------------------------------------------------------
# Test 16-17: generate_recommendations integration
# ---------------------------------------------------------------------------


def test_generate_empty_analysis():
    """Empty RunAnalysis → empty result."""
    analysis = _make_analysis()
    result = generate_recommendations(analysis, {})
    assert result.recommendations == []
    assert result.analysis_run_id == "test-run-1"


def test_generate_multiple_recommendations():
    """Multiple signals → multiple recs across categories."""
    analysis = _make_analysis(
        validators=[
            _make_validator("bandit", pass_rate=20.0, call_count=5, fail_count=4),
            _make_validator("ruff", pass_rate=100.0, call_count=8, fail_count=0),
        ],
        repair_categories=[
            _make_repair_cat("lint_error", occurrences=4, resolved_count=1,
                             resolution_rate=25.0),
        ],
        total_repair_loops=6,
    )
    result = generate_recommendations(analysis, {})
    assert len(result.recommendations) >= 3

    categories = {r.category for r in result.recommendations}
    assert TuningCategory.SOFT_FAIL in categories
    assert TuningCategory.ESCALATION in categories
    assert TuningCategory.PARALLEL_FIXER in categories


# ---------------------------------------------------------------------------
# Test 18-19: Graceful degradation
# ---------------------------------------------------------------------------


def test_generate_none_quality_profile():
    """quality_profile=None → works, uses defaults."""
    analysis = _make_analysis(validators=[
        _make_validator("bandit", pass_rate=10.0, call_count=3, fail_count=3),
    ])
    result = generate_recommendations(analysis, None)
    assert len(result.recommendations) >= 1


def test_tuner_result_to_dict():
    """to_dict() produces correct JSON structure."""
    analysis = _make_analysis(validators=[
        _make_validator("bandit", pass_rate=20.0, call_count=5, fail_count=4),
    ])
    result = generate_recommendations(analysis, {})
    d = result.to_dict()

    assert d["analysis_run_id"] == "test-run-1"
    assert isinstance(d["recommendation_count"], int)
    assert isinstance(d["recommendations"], list)
    assert d["recommendation_count"] == len(d["recommendations"])

    if d["recommendations"]:
        rec = d["recommendations"][0]
        assert "parameter_path" in rec
        assert "current_value" in rec
        assert "recommended_value" in rec
        assert "reason" in rec
        assert "confidence" in rec
        assert "category" in rec


# ---------------------------------------------------------------------------
# Test 20: format_recommendations smoke test
# ---------------------------------------------------------------------------


def test_format_recommendations_smoke():
    """Non-empty result → output contains section headings."""
    analysis = _make_analysis(
        validators=[
            _make_validator("bandit", pass_rate=20.0, call_count=5, fail_count=4),
        ],
        total_repair_loops=6,
    )
    result = generate_recommendations(analysis, {})
    text = format_recommendations(result)

    assert "CONFIG TUNER RECOMMENDATIONS" in text
    assert "Parameter:" in text
    assert "Suggested:" in text
    assert "Confidence:" in text
    assert "SOFT_FAIL" in text
