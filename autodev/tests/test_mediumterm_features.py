"""Tests for medium-term autoresearch features: score-based strategy, baseline trending, multi-strategy."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from autodev.config import _validate_run_section


# ---------------------------------------------------------------------------
# Feature 2: Score-based adaptive fix strategy routing
# ---------------------------------------------------------------------------


class TestRouteStrategyFromComponentScores:
    """Tests for _route_strategy_from_component_scores()."""

    def _make_score(
        self,
        tests: float = 90.0,
        lint: float = 90.0,
        type_health: float = 90.0,
        security: float = 90.0,
        simplicity: float = 90.0,
    ):
        from autodev.quality_score import QualityScore
        return QualityScore(
            composite=80.0,
            hard_blocked=False,
            hard_blockers=[],
            tests_score=tests,
            lint_score=lint,
            type_health_score=type_health,
            security_score=security,
            simplicity_score=simplicity,
        )

    def test_all_high_returns_mixed(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score()
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "mixed"

    def test_low_tests_score_returns_tests_focused(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(tests=30.0)
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "tests-focused"
        assert result["weakest_component"] == "tests_score"
        assert result["weakest_value"] == 30.0

    def test_low_security_score_returns_security_focused(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(security=20.0)
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "security-focused"
        assert result["weakest_component"] == "security_score"

    def test_low_lint_returns_tests_focused(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(lint=40.0)
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "tests-focused"
        assert result["weakest_component"] == "lint_score"

    def test_low_type_health_returns_tests_focused(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(type_health=50.0)
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "tests-focused"
        assert result["weakest_component"] == "type_health_score"

    def test_multiple_weak_picks_lowest(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(tests=50.0, security=25.0)
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "security-focused"
        assert result["weakest_component"] == "security_score"
        assert result["weakest_value"] == 25.0

    def test_none_quality_score_returns_mixed(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        result = _route_strategy_from_component_scores(None)
        assert result["recommended"] == "mixed"

    def test_custom_weak_threshold(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(tests=55.0)
        # Default threshold 60.0 => tests=55 is weak
        result = _route_strategy_from_component_scores(score)
        assert result["recommended"] == "tests-focused"
        # Higher threshold 50.0 => tests=55 is NOT weak
        result2 = _route_strategy_from_component_scores(score, weak_threshold=50.0)
        assert result2["recommended"] == "mixed"

    def test_exactly_at_threshold_not_weak(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(tests=60.0)
        result = _route_strategy_from_component_scores(score, weak_threshold=60.0)
        assert result["recommended"] == "mixed"

    def test_source_field_is_component_scores(self):
        from autodev.autonomous_mode import _route_strategy_from_component_scores
        score = self._make_score(tests=30.0)
        result = _route_strategy_from_component_scores(score)
        assert result["source"] == "component_scores"


class TestResolveRetryStrategyWithComponentScores:
    """Tests for _resolve_retry_strategy integration with quality_score param."""

    def _make_score(self, tests: float = 90.0, security: float = 90.0, **kw):
        from autodev.quality_score import QualityScore
        return QualityScore(
            composite=80.0,
            hard_blocked=False,
            hard_blockers=[],
            tests_score=tests,
            lint_score=kw.get("lint", 90.0),
            type_health_score=kw.get("type_health", 90.0),
            security_score=security,
            simplicity_score=kw.get("simplicity", 90.0),
        )

    def test_initial_iteration_returns_mixed(self):
        from autodev.autonomous_mode import _resolve_retry_strategy
        result = _resolve_retry_strategy([], 1)
        assert result["name"] == "mixed"
        assert result["selected_by"] == "initial_default"

    def test_second_iteration_no_score_uses_gate_routing(self):
        from autodev.autonomous_mode import _resolve_retry_strategy
        attempts = [{"gate_results": {"fail_reasons": []}}]
        result = _resolve_retry_strategy(attempts, 2)
        assert result["selected_by"] == "gate_fail_routing"

    def test_second_iteration_with_low_tests_score_routes_to_tests(self):
        from autodev.autonomous_mode import _resolve_retry_strategy
        score = self._make_score(tests=30.0)
        attempts = [{"gate_results": {"fail_reasons": []}}]
        result = _resolve_retry_strategy(attempts, 2, quality_score=score)
        assert result["recommended"] == "tests-focused"
        assert result["selected_by"] == "component_scores"

    def test_gate_routing_takes_precedence_over_component_scores(self):
        from autodev.autonomous_mode import _resolve_retry_strategy
        # If gate routing gives a specific strategy, component scores are NOT consulted
        score = self._make_score(tests=30.0)
        attempts = [{"gate_results": {"fail_reasons": [
            {"code": "security.max_high_findings_exceeded", "category": "security", "gate_name": "security"}
        ]}}]
        result = _resolve_retry_strategy(attempts, 2, quality_score=score)
        # Gate routing should give security-focused, not tests-focused from component scores
        assert result["recommended"] == "security-focused"

    def test_quality_score_none_falls_through_to_mixed(self):
        from autodev.autonomous_mode import _resolve_retry_strategy
        attempts = [{"gate_results": {"fail_reasons": []}}]
        result = _resolve_retry_strategy(attempts, 2, quality_score=None)
        assert result["recommended"] == "mixed"
        assert result["selected_by"] == "gate_fail_routing"


# ---------------------------------------------------------------------------
# Feature 3: Baseline trending - quality metrics in perf baseline
# ---------------------------------------------------------------------------


class TestRunMetricsSnapshotQualityFields:
    """Tests for extended RunMetricsSnapshot with quality fields."""

    def test_quality_fields_exist(self):
        from autodev.perf_baseline import RunMetricsSnapshot
        snap = RunMetricsSnapshot(
            run_id="test_001",
            timestamp="2026-01-01T00:00:00Z",
            profile="default",
            total_elapsed_ms=1000,
            phase_durations_ms={},
            total_llm_prompt_tokens=0,
            total_llm_completion_tokens=0,
            total_llm_tokens=0,
            total_llm_calls=0,
            total_llm_retries=0,
            total_validation_ms=0,
            max_task_ms=0,
            p95_task_ms=0,
            median_task_ms=0,
            task_count=1,
            passed_tasks=1,
            failed_tasks=0,
            total_task_attempts=1,
            repair_passes=0,
        )
        # Quality fields should have defaults
        assert snap.composite_score_avg == 0.0
        assert snap.composite_score_min == 0.0
        assert snap.tests_score_avg == 0.0
        assert snap.lint_score_avg == 0.0
        assert snap.type_health_score_avg == 0.0
        assert snap.security_score_avg == 0.0
        assert snap.hard_blocked_tasks == 0
        assert snap.reverted_attempts == 0
        assert snap.accepted_attempts == 0

    def test_quality_fields_in_to_dict(self):
        from autodev.perf_baseline import RunMetricsSnapshot
        snap = RunMetricsSnapshot(
            run_id="test_001",
            timestamp="2026-01-01T00:00:00Z",
            profile="default",
            total_elapsed_ms=1000,
            phase_durations_ms={},
            total_llm_prompt_tokens=0,
            total_llm_completion_tokens=0,
            total_llm_tokens=0,
            total_llm_calls=0,
            total_llm_retries=0,
            total_validation_ms=0,
            max_task_ms=0,
            p95_task_ms=0,
            median_task_ms=0,
            task_count=1,
            passed_tasks=1,
            failed_tasks=0,
            total_task_attempts=1,
            repair_passes=0,
            composite_score_avg=85.0,
            composite_score_min=70.0,
        )
        d = snap.to_dict()
        assert d["composite_score_avg"] == 85.0
        assert d["composite_score_min"] == 70.0


class TestCollectRunMetricsQuality:
    """Tests for quality data extraction in collect_run_metrics."""

    def test_extracts_quality_from_experiment_log(self):
        from autodev.perf_baseline import collect_run_metrics
        quality_summary = {
            "tasks": [],
            "totals": {},
            "experiment_log": {
                "entry_count": 3,
                "tasks": {
                    "t1": {"attempts": 2, "decisions": {"accepted": 1, "reverted": 1, "neutral": 0}, "best_score": 85.0, "final_score": 85.0},
                    "t2": {"attempts": 1, "decisions": {"accepted": 1, "reverted": 0, "neutral": 0}, "best_score": 70.0, "final_score": 70.0},
                },
            },
        }
        snap = collect_run_metrics("run1", "default", {}, quality_summary)
        assert snap.composite_score_avg == pytest.approx(77.5, abs=0.1)
        assert snap.composite_score_min == 70.0
        assert snap.accepted_attempts == 2
        assert snap.reverted_attempts == 1

    def test_no_experiment_log_gives_zero_quality(self):
        from autodev.perf_baseline import collect_run_metrics
        snap = collect_run_metrics("run1", "default", {}, {"tasks": [], "totals": {}})
        assert snap.composite_score_avg == 0.0
        assert snap.composite_score_min == 0.0


class TestDetectRegressionDirectional:
    """Tests for direction-aware regression detection (higher_is_better)."""

    def _make_snap(self, composite_avg=85.0, composite_min=70.0, **kw):
        from autodev.perf_baseline import RunMetricsSnapshot
        return RunMetricsSnapshot(
            run_id="current",
            timestamp="2026-01-01T00:00:00Z",
            profile="default",
            total_elapsed_ms=kw.get("total_elapsed_ms", 1000),
            phase_durations_ms={},
            total_llm_prompt_tokens=0,
            total_llm_completion_tokens=0,
            total_llm_tokens=kw.get("total_llm_tokens", 0),
            total_llm_calls=0,
            total_llm_retries=0,
            total_validation_ms=kw.get("total_validation_ms", 500),
            max_task_ms=kw.get("max_task_ms", 500),
            p95_task_ms=0,
            median_task_ms=0,
            task_count=1,
            passed_tasks=1,
            failed_tasks=0,
            total_task_attempts=1,
            repair_passes=0,
            composite_score_avg=composite_avg,
            composite_score_min=composite_min,
        )

    def test_quality_decline_detected_as_regression(self):
        from autodev.perf_baseline import detect_regression
        # Baseline with avg=85, current=60 => decline of ~29% => regression
        baseline = {
            "schema_version": 2,
            "runs": [
                self._make_snap(composite_avg=85.0, composite_min=80.0).to_dict(),
                self._make_snap(composite_avg=85.0, composite_min=80.0).to_dict(),
            ],
        }
        current = self._make_snap(composite_avg=60.0, composite_min=50.0)
        result = detect_regression(current, baseline)
        # composite_score_avg should be flagged (decline > 15%)
        quality_verdicts = [v for v in result.verdicts if v.metric_name == "composite_score_avg"]
        assert len(quality_verdicts) == 1
        assert not quality_verdicts[0].ok

    def test_quality_improvement_not_flagged(self):
        from autodev.perf_baseline import detect_regression
        baseline = {
            "schema_version": 2,
            "runs": [
                self._make_snap(composite_avg=70.0, composite_min=60.0).to_dict(),
            ],
        }
        current = self._make_snap(composite_avg=85.0, composite_min=75.0)
        result = detect_regression(current, baseline)
        quality_verdicts = [v for v in result.verdicts if v.metric_name == "composite_score_avg"]
        assert len(quality_verdicts) == 1
        assert quality_verdicts[0].ok

    def test_stable_quality_passes(self):
        from autodev.perf_baseline import detect_regression
        baseline = {
            "schema_version": 2,
            "runs": [
                self._make_snap(composite_avg=80.0).to_dict(),
                self._make_snap(composite_avg=82.0).to_dict(),
            ],
        }
        current = self._make_snap(composite_avg=79.0)
        result = detect_regression(current, baseline)
        quality_verdicts = [v for v in result.verdicts if v.metric_name == "composite_score_avg"]
        assert len(quality_verdicts) == 1
        assert quality_verdicts[0].ok


class TestSchemaVersion2Migration:
    """Tests for schema version migration handling."""

    def test_v1_baseline_resets_gracefully(self):
        from autodev.perf_baseline import _read_baseline
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"schema_version": 1, "runs": [{"run_id": "old"}]}, f)
            f.flush()
            result = _read_baseline(f.name)
        os.unlink(f.name)
        # v1 baseline should be reset since we're now at v2
        assert result["schema_version"] == 2
        assert result["runs"] == []

    def test_v2_baseline_loaded(self):
        from autodev.perf_baseline import _read_baseline
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"schema_version": 2, "runs": [{"run_id": "recent"}]}, f)
            f.flush()
            result = _read_baseline(f.name)
        os.unlink(f.name)
        assert result["schema_version"] == 2
        assert len(result["runs"]) == 1


class TestQualityThresholds:
    """Tests for quality metric thresholds in DEFAULT_THRESHOLDS."""

    def test_quality_thresholds_exist(self):
        from autodev.perf_baseline import DEFAULT_THRESHOLDS
        assert "composite_score_avg" in DEFAULT_THRESHOLDS
        assert "composite_score_min" in DEFAULT_THRESHOLDS

    def test_quality_thresholds_have_direction(self):
        from autodev.perf_baseline import DEFAULT_THRESHOLDS
        assert DEFAULT_THRESHOLDS["composite_score_avg"]["direction"] == "higher_is_better"
        assert DEFAULT_THRESHOLDS["composite_score_min"]["direction"] == "higher_is_better"

    def test_perf_thresholds_default_lower_is_better(self):
        from autodev.perf_baseline import DEFAULT_THRESHOLDS
        # Legacy perf metrics have no direction field or "lower_is_better"
        for metric in ("total_elapsed_ms", "total_validation_ms"):
            direction = DEFAULT_THRESHOLDS[metric].get("direction", "lower_is_better")
            assert direction == "lower_is_better"


# ---------------------------------------------------------------------------
# Feature 1: Multi-strategy exploration
# ---------------------------------------------------------------------------


class TestMultiStrategyConfig:
    """Tests for multi-strategy config validation."""

    def test_valid_multi_strategy_config(self):
        run: Dict[str, Any] = {
            "multi_strategy": {
                "enabled": True,
                "strategies": 3,
                "min_failed_attempts": 2,
                "score_margin": 5.0,
            }
        }
        errors: List[str] = []
        _validate_run_section(run, errors)
        assert not errors

    def test_multi_strategy_unknown_key(self):
        run: Dict[str, Any] = {
            "multi_strategy": {"enabled": True, "bogus": 42}
        }
        errors: List[str] = []
        _validate_run_section(run, errors)
        assert any("unknown key" in e for e in errors)

    def test_strategies_out_of_range(self):
        run: Dict[str, Any] = {
            "multi_strategy": {"strategies": 5}
        }
        errors: List[str] = []
        _validate_run_section(run, errors)
        assert any("between 2 and 4" in e for e in errors)

    def test_negative_score_margin(self):
        run: Dict[str, Any] = {
            "multi_strategy": {"score_margin": -1.0}
        }
        errors: List[str] = []
        _validate_run_section(run, errors)
        assert any(">= 0" in e for e in errors)

    def test_multi_strategy_not_required(self):
        run: Dict[str, Any] = {}
        errors: List[str] = []
        _validate_run_section(run, errors)
        assert not errors


class TestMultiStrategyResolveConfig:
    """Tests for resolve_multi_strategy_config."""

    def test_no_config_returns_disabled(self):
        from autodev.multi_strategy import resolve_multi_strategy_config
        cfg = resolve_multi_strategy_config(None)
        assert not cfg.enabled
        assert cfg.strategies == 2

    def test_extracts_from_quality_profile(self):
        from autodev.multi_strategy import resolve_multi_strategy_config
        profile = {
            "run": {
                "multi_strategy": {
                    "enabled": True,
                    "strategies": 3,
                    "min_failed_attempts": 1,
                    "score_margin": 10.0,
                }
            }
        }
        cfg = resolve_multi_strategy_config(profile)
        assert cfg.enabled
        assert cfg.strategies == 3
        assert cfg.min_failed_attempts == 1
        assert cfg.score_margin == 10.0


class TestShouldExplore:
    """Tests for should_explore decision logic."""

    def test_disabled_config_returns_false(self):
        from autodev.multi_strategy import MultiStrategyConfig, should_explore
        cfg = MultiStrategyConfig(enabled=False)
        assert not should_explore(cfg, consecutive_failures=5)

    def test_not_enough_failures_returns_false(self):
        from autodev.multi_strategy import MultiStrategyConfig, should_explore
        cfg = MultiStrategyConfig(enabled=True, min_failed_attempts=3)
        assert not should_explore(cfg, consecutive_failures=2)

    def test_enough_failures_returns_true(self):
        from autodev.multi_strategy import MultiStrategyConfig, should_explore
        cfg = MultiStrategyConfig(enabled=True, min_failed_attempts=2)
        assert should_explore(cfg, consecutive_failures=2)


class TestExploreStrategies:
    """Tests for explore_strategies async function."""

    def _make_score(self, composite: float, hard_blocked: bool = False):
        from autodev.quality_score import QualityScore
        return QualityScore(
            composite=composite,
            hard_blocked=hard_blocked,
            hard_blockers=["blocker"] if hard_blocked else [],
        )

    def test_picks_best_strategy(self):
        from autodev.multi_strategy import explore_strategies

        scores = {"tests-focused": 85.0, "security-focused": 70.0}

        async def mock_fixer(name):
            return {"changes": [], "strategy": name}

        def mock_apply(changeset):
            pass

        async def mock_validate():
            return []

        current_strategy = [None]

        def mock_score(rows):
            return self._make_score(scores.get(current_strategy[0], 50.0))

        def mock_rollback():
            pass

        # We need to track which strategy is being evaluated
        original_explore = explore_strategies

        async def run():
            result = await explore_strategies(
                strategy_names=["tests-focused", "security-focused"],
                run_fixer=mock_fixer,
                apply_changeset=mock_apply,
                run_validators=mock_validate,
                compute_score=lambda rows: self._make_score(85.0),
                snapshot_rollback=mock_rollback,
                baseline_score=60.0,
                score_margin=5.0,
            )
            return result

        result = asyncio.run(run())
        assert result.explored
        assert result.winner is not None
        assert result.winner.composite == 85.0

    def test_no_winner_below_margin(self):
        from autodev.multi_strategy import explore_strategies

        async def mock_fixer(name):
            return {"changes": []}

        async def mock_validate():
            return []

        async def run():
            result = await explore_strategies(
                strategy_names=["tests-focused"],
                run_fixer=mock_fixer,
                apply_changeset=lambda c: None,
                run_validators=mock_validate,
                compute_score=lambda rows: self._make_score(62.0),
                snapshot_rollback=lambda: None,
                baseline_score=60.0,
                score_margin=5.0,
            )
            return result

        result = asyncio.run(run())
        assert result.explored
        assert result.winner is None  # 62 < 60 + 5

    def test_hard_blocked_not_winner(self):
        from autodev.multi_strategy import explore_strategies

        async def mock_fixer(name):
            return {"changes": []}

        async def mock_validate():
            return []

        async def run():
            result = await explore_strategies(
                strategy_names=["tests-focused"],
                run_fixer=mock_fixer,
                apply_changeset=lambda c: None,
                run_validators=mock_validate,
                compute_score=lambda rows: self._make_score(90.0, hard_blocked=True),
                snapshot_rollback=lambda: None,
                baseline_score=60.0,
                score_margin=5.0,
            )
            return result

        result = asyncio.run(run())
        assert result.winner is None

    def test_fixer_exception_handled(self):
        from autodev.multi_strategy import explore_strategies

        async def mock_fixer(name):
            raise RuntimeError("LLM error")

        async def mock_validate():
            return []

        async def run():
            result = await explore_strategies(
                strategy_names=["tests-focused"],
                run_fixer=mock_fixer,
                apply_changeset=lambda c: None,
                run_validators=mock_validate,
                compute_score=lambda rows: self._make_score(80.0),
                snapshot_rollback=lambda: None,
                baseline_score=60.0,
            )
            return result

        result = asyncio.run(run())
        assert result.explored
        assert len(result.strategy_results) == 1
        assert result.strategy_results[0].error is not None
        assert result.winner is None


# ---------------------------------------------------------------------------
# Feature 2 config: weak_threshold validation (deferred from earlier)
# ---------------------------------------------------------------------------


class TestMultiStrategyEventType:
    """Test that multi-strategy event type exists in run_trace."""

    def test_event_type_exists(self):
        from autodev.run_trace import EventType
        assert EventType.MULTI_STRATEGY_EXPLORED == "multi_strategy.explored"
