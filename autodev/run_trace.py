"""Structured run trace collector for observability and telemetry.

Accumulates typed events, phase timings, and LLM call metrics in-memory.
Serialised to ``.autodev/run_trace.json`` at run end via :meth:`RunTrace.to_dict`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Event taxonomy
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Typed event categories for structured telemetry."""

    # Run lifecycle
    RUN_START = "run.start"
    RUN_COMPLETED = "run.completed"

    # Pipeline phases
    PHASE_START = "phase.start"
    PHASE_END = "phase.end"

    # Task execution
    TASK_START = "task.start"
    TASK_PASSED = "task.passed"
    TASK_FAILED = "task.failed"
    TASK_SKIPPED = "task.skipped"
    TASK_FAILED_CONTINUING = "task.failed_continuing"

    # Validation
    VALIDATION_START = "validation.start"
    VALIDATION_RESULT = "validation.result"
    VALIDATION_DEDUP = "validation.dedup"

    # LLM calls
    LLM_CALL_START = "llm.call_start"
    LLM_CALL_END = "llm.call_end"
    LLM_BUDGET_CHECK = "llm.budget_check"

    # Plugin system
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_ERROR = "plugin.error"

    # Concurrency
    BATCH_PARALLEL_START = "batch.parallel_start"
    BATCH_PARALLEL_END = "batch.parallel_end"
    CONCURRENCY_ADJUSTED = "concurrency.adjusted"

    # Snapshots
    SNAPSHOT_CREATED = "snapshot.created"
    SNAPSHOT_ROLLBACK = "snapshot.rollback"

    # Context cache
    CONTEXT_CACHE_HIT = "context_cache.hit"
    CONTEXT_CACHE_SUMMARY = "context_cache.summary"

    # Parallel fixer
    PARALLEL_FIXER_PLANNED = "parallel_fixer.planned"
    PARALLEL_FIXER_MERGED = "parallel_fixer.merged"

    # Smart scope
    SMART_SCOPE_APPLIED = "smart_scope.applied"

    # Config tuner
    CONFIG_TUNER_ANALYZED = "config_tuner.analyzed"

    # Validator dependency graph
    VALIDATOR_DEP_SKIPPED = "validator_dep.skipped"

    # Quality score (autoresearch-inspired experiment tracking)
    QUALITY_SCORE_COMPUTED = "quality_score.computed"
    EXPERIMENT_DECISION = "experiment.decision"

    # Time budget
    TASK_TIME_BUDGET_EXCEEDED = "task.time_budget_exceeded"

    # Multi-strategy exploration
    MULTI_STRATEGY_EXPLORED = "multi_strategy.explored"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TraceEvent:
    """Single structured event in a run trace."""

    event_type: EventType
    timestamp: str  # ISO-8601
    elapsed_ms: int  # ms since run start
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseTimings:
    """Timing record for a named pipeline phase."""

    phase: str
    start_ms: int
    end_ms: int = 0
    duration_ms: int = 0
    status: str = "running"  # running | completed | failed


@dataclass
class LLMCallMetrics:
    """Aggregated LLM metrics per role."""

    role: str
    call_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_duration_ms: int = 0
    retry_count: int = 0


# ---------------------------------------------------------------------------
# RunTrace
# ---------------------------------------------------------------------------


@dataclass
class RunTrace:
    """In-memory trace accumulator for a single autodev run.

    Collects typed events, phase timings, and LLM metrics.
    Caller is responsible for flushing via ``to_dict()`` + file write.
    """

    run_id: str
    request_id: str
    profile: Optional[str] = None
    events: List[TraceEvent] = field(default_factory=list)
    phases: List[PhaseTimings] = field(default_factory=list)
    llm_metrics: Dict[str, LLMCallMetrics] = field(default_factory=dict)
    _start_time: float = field(default_factory=time.monotonic, repr=False)
    _active_phases: Dict[str, PhaseTimings] = field(default_factory=dict, repr=False)

    # -- helpers ---------------------------------------------------------------

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start_time) * 1000)

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

    # -- event recording -------------------------------------------------------

    def record(self, event_type: EventType, **data: Any) -> TraceEvent:
        """Record a typed event with automatic timestamping."""
        ev = TraceEvent(
            event_type=event_type,
            timestamp=self._now_iso(),
            elapsed_ms=self._elapsed_ms(),
            data=data,
        )
        self.events.append(ev)
        return ev

    # -- phase timing ----------------------------------------------------------

    def start_phase(self, phase: str) -> None:
        """Begin timing a named pipeline phase."""
        timing = PhaseTimings(phase=phase, start_ms=self._elapsed_ms())
        self._active_phases[phase] = timing
        self.phases.append(timing)
        self.record(EventType.PHASE_START, phase=phase)

    def end_phase(self, phase: str, status: str = "completed") -> Optional[PhaseTimings]:
        """End timing for a named phase. Returns the PhaseTimings or None."""
        timing = self._active_phases.pop(phase, None)
        if timing is None:
            return None
        timing.end_ms = self._elapsed_ms()
        timing.duration_ms = timing.end_ms - timing.start_ms
        timing.status = status
        self.record(EventType.PHASE_END, phase=phase, duration_ms=timing.duration_ms, status=status)
        return timing

    # -- LLM call metrics ------------------------------------------------------

    def record_llm_call(
        self,
        role: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int = 0,
        is_retry: bool = False,
    ) -> None:
        """Accumulate LLM call metrics for a role."""
        if role not in self.llm_metrics:
            self.llm_metrics[role] = LLMCallMetrics(role=role)
        m = self.llm_metrics[role]
        m.call_count += 1
        m.total_prompt_tokens += prompt_tokens
        m.total_completion_tokens += completion_tokens
        m.total_duration_ms += duration_ms
        if is_retry:
            m.retry_count += 1

    # -- serialisation ---------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the entire trace for JSON output."""
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "profile": self.profile,
            "total_elapsed_ms": self._elapsed_ms(),
            "event_count": len(self.events),
            "events": [
                {
                    "event_type": ev.event_type.value,
                    "timestamp": ev.timestamp,
                    "elapsed_ms": ev.elapsed_ms,
                    **ev.data,
                }
                for ev in self.events
            ],
            "phases": [
                {
                    "phase": p.phase,
                    "start_ms": p.start_ms,
                    "end_ms": p.end_ms,
                    "duration_ms": p.duration_ms,
                    "status": p.status,
                }
                for p in self.phases
            ],
            "llm_metrics": {
                role: {
                    "call_count": m.call_count,
                    "total_prompt_tokens": m.total_prompt_tokens,
                    "total_completion_tokens": m.total_completion_tokens,
                    "total_duration_ms": m.total_duration_ms,
                    "retry_count": m.retry_count,
                }
                for role, m in self.llm_metrics.items()
            },
        }
