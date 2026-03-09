"""Multi-strategy exploration for autonomous fix loops.

When a task fails multiple consecutive attempts, this module tries 2-3 different
fix strategies in parallel (LLM calls), then sequentially applies each changeset,
validates, scores, and picks the winner.

Pattern: parallel LLM calls → sequential apply-validate-score-rollback → apply winner.

Inspired by autoresearch's multi-experiment approach: try several hypotheses,
evaluate on a single metric, keep the best.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger("autodev")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MultiStrategyConfig:
    """Configuration for multi-strategy exploration."""

    enabled: bool = False
    strategies: int = 2           # number of parallel strategies (2-3)
    min_failed_attempts: int = 2  # only activate after N sequential failures
    score_margin: float = 5.0     # minimum composite score advantage to pick winner


@dataclass
class StrategyResult:
    """Result of evaluating one strategy."""

    strategy_name: str
    changeset: Dict[str, Any]
    quality_score: Any  # QualityScore instance
    validation_rows: List[Dict[str, Any]]
    wall_clock_ms: int
    error: Optional[str] = None

    @property
    def composite(self) -> float:
        if self.quality_score is None:
            return 0.0
        return float(getattr(self.quality_score, "composite", 0.0))

    @property
    def hard_blocked(self) -> bool:
        if self.quality_score is None:
            return True
        return bool(getattr(self.quality_score, "hard_blocked", True))


@dataclass
class ExplorationResult:
    """Result of a multi-strategy exploration run."""

    explored: bool = False
    strategy_results: List[StrategyResult] = field(default_factory=list)
    winner: Optional[StrategyResult] = None
    winner_reason: str = ""
    baseline_score: float = 0.0


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def resolve_multi_strategy_config(
    quality_profile: Dict[str, Any] | None,
) -> MultiStrategyConfig:
    """Extract multi-strategy config from quality_profile."""
    if not isinstance(quality_profile, dict):
        return MultiStrategyConfig()

    run_cfg = quality_profile.get("run")
    if not isinstance(run_cfg, dict):
        return MultiStrategyConfig()

    ms_cfg = run_cfg.get("multi_strategy")
    if not isinstance(ms_cfg, dict):
        return MultiStrategyConfig()

    return MultiStrategyConfig(
        enabled=bool(ms_cfg.get("enabled", False)),
        strategies=max(2, min(int(ms_cfg.get("strategies", 2)), 4)),
        min_failed_attempts=max(1, int(ms_cfg.get("min_failed_attempts", 2))),
        score_margin=float(ms_cfg.get("score_margin", 5.0)),
    )


# ---------------------------------------------------------------------------
# Strategy exploration
# ---------------------------------------------------------------------------


async def explore_strategies(
    strategy_names: List[str],
    run_fixer: Callable[[str], Awaitable[Dict[str, Any]]],
    apply_changeset: Callable[[Dict[str, Any]], None],
    run_validators: Callable[[], Awaitable[List[Dict[str, Any]]]],
    compute_score: Callable[[List[Dict[str, Any]]], Any],
    snapshot_rollback: Callable[[], None],
    baseline_score: float = 0.0,
    score_margin: float = 5.0,
) -> ExplorationResult:
    """Explore multiple fix strategies and pick the best.

    Parameters
    ----------
    strategy_names:
        List of strategy names to try (e.g. ["tests-focused", "security-focused"]).
    run_fixer:
        Async callable(strategy_name) -> changeset dict. Runs the LLM fixer.
    apply_changeset:
        Callable(changeset) -> None. Applies changeset to workspace.
    run_validators:
        Async callable() -> validation_rows. Runs validators on workspace.
    compute_score:
        Callable(validation_rows) -> QualityScore. Computes quality score.
    snapshot_rollback:
        Callable() -> None. Rolls back workspace to pre-exploration snapshot.
    baseline_score:
        Current best composite score (to compare against).
    score_margin:
        Minimum improvement over baseline required to select a winner.

    Returns
    -------
    ExplorationResult with ranked strategies and optional winner.
    """
    result = ExplorationResult(explored=True, baseline_score=baseline_score)

    # Phase 1: Parallel LLM calls for all strategies
    logger.info("multi_strategy: launching %d parallel fixer calls", len(strategy_names))
    fixer_coros = [run_fixer(name) for name in strategy_names]
    fixer_results = await asyncio.gather(*fixer_coros, return_exceptions=True)

    # Phase 2: Sequential apply → validate → score → rollback for each
    for i, (name, fixer_result) in enumerate(zip(strategy_names, fixer_results)):
        if isinstance(fixer_result, Exception):
            logger.warning("multi_strategy: strategy %s fixer failed: %s", name, fixer_result)
            result.strategy_results.append(StrategyResult(
                strategy_name=name,
                changeset={},
                quality_score=None,
                validation_rows=[],
                wall_clock_ms=0,
                error=str(fixer_result),
            ))
            continue

        t0 = time.perf_counter()
        try:
            # Rollback to clean state
            snapshot_rollback()

            # Apply changeset
            apply_changeset(fixer_result)

            # Validate
            validation_rows = await run_validators()

            # Score
            quality_score = compute_score(validation_rows)

            wall_ms = int((time.perf_counter() - t0) * 1000)

            result.strategy_results.append(StrategyResult(
                strategy_name=name,
                changeset=fixer_result,
                quality_score=quality_score,
                validation_rows=validation_rows,
                wall_clock_ms=wall_ms,
            ))
            logger.info(
                "multi_strategy: strategy %s scored %.1f (hard_blocked=%s)",
                name, quality_score.composite if quality_score else 0,
                quality_score.hard_blocked if quality_score else True,
            )

        except Exception as exc:
            wall_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("multi_strategy: strategy %s eval failed: %s", name, exc)
            result.strategy_results.append(StrategyResult(
                strategy_name=name,
                changeset=fixer_result,
                quality_score=None,
                validation_rows=[],
                wall_clock_ms=wall_ms,
                error=str(exc),
            ))

    # Rollback after all evaluations
    try:
        snapshot_rollback()
    except Exception:
        pass

    # Phase 3: Pick winner
    valid_results = [
        sr for sr in result.strategy_results
        if sr.quality_score is not None and not sr.hard_blocked
    ]

    if not valid_results:
        result.winner_reason = "No strategy produced a non-blocked result."
        return result

    # Sort by composite score descending
    valid_results.sort(key=lambda sr: sr.composite, reverse=True)
    best = valid_results[0]

    if best.composite >= baseline_score + score_margin:
        result.winner = best
        result.winner_reason = (
            f"Strategy '{best.strategy_name}' scored {best.composite:.1f}, "
            f"beating baseline {baseline_score:.1f} by {best.composite - baseline_score:.1f} "
            f"(margin={score_margin})."
        )
    elif best.composite > baseline_score:
        result.winner_reason = (
            f"Best strategy '{best.strategy_name}' scored {best.composite:.1f}, "
            f"above baseline {baseline_score:.1f} but below margin {score_margin}."
        )
    else:
        result.winner_reason = (
            f"No strategy improved on baseline {baseline_score:.1f}. "
            f"Best was '{best.strategy_name}' at {best.composite:.1f}."
        )

    return result


def should_explore(
    config: MultiStrategyConfig,
    consecutive_failures: int,
) -> bool:
    """Check if multi-strategy exploration should be triggered."""
    return (
        config.enabled
        and consecutive_failures >= config.min_failed_attempts
        and config.strategies >= 2
    )
