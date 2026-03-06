#!/usr/bin/env python3
"""Generate deterministic showoff fixtures under generated_runs/.

Outputs artifacts consumed by both:
- autodev.gui_mvp_server (quality/validation/run_trace)
- autodev.gui_api (run_metadata/checkpoint/run_trace)
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_TIME = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FixtureSpec:
    run_id: str
    status: str
    profile: str
    model: str
    ended: bool


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iso(ts: datetime | None) -> str:
    return ts.isoformat().replace("+00:00", "Z") if ts else ""


def _run_trace(run_id: str, profile: str, model: str, started: datetime, ended: datetime | None) -> dict[str, Any]:
    phases = [
        {"phase": "prd_analysis", "duration_ms": 2_000, "status": "completed"},
        {"phase": "architecture", "duration_ms": 3_000, "status": "completed"},
        {"phase": "planning", "duration_ms": 3_000, "status": "completed"},
        {"phase": "implementation", "duration_ms": 8_000, "status": "completed" if ended else "running"},
    ]
    if ended:
        phases.append({"phase": "final_validation", "duration_ms": 9_000, "status": "completed" if ended else "running"})

    events: list[dict[str, Any]] = [{"event_type": "run.start", "timestamp": _iso(started)}]
    if ended:
        events.append({"event_type": "run.completed", "timestamp": _iso(ended)})

    return {
        "run_id": run_id,
        "request_id": f"req-{run_id}",
        "profile": profile,
        "llm": {"model": model},
        "run_started_at": _iso(started),
        "run_completed_at": _iso(ended),
        "total_elapsed_ms": int((ended - started).total_seconds() * 1000) if ended else 0,
        "event_count": len(events),
        "events": events,
        "phase_timeline": phases,
    }


def _quality(status: str, profile: str, project_type: str = "python_cli") -> dict[str, Any]:
    hard = 0 if status == "ok" else 1
    final_status = "running" if status == "running" else status
    return {
        "project": {"type": project_type},
        "resolved_quality_profile": {"name": profile},
        "totals": {"total_task_attempts": 3, "hard_failures": hard, "soft_failures": 0},
        "final": {"status": final_status},
        "unresolved_blockers": [] if status == "ok" else ["final_validation"],
        "tasks": [
            {"task_id": "setup", "attempt": 1, "status": "passed", "hard_failures": 0, "soft_failures": 0, "duration_ms": 1200},
            {"task_id": "core", "attempt": 1, "status": "passed" if status == "ok" else "failed", "hard_failures": hard, "soft_failures": 0, "duration_ms": 3200},
        ],
    }


def _final_validation(ok: bool) -> dict[str, Any]:
    return {
        "validation": [
            {"name": "ruff", "ok": ok, "status": "passed" if ok else "failed", "returncode": 0 if ok else 1, "duration_ms": 1200},
            {"name": "pytest", "ok": ok, "status": "passed" if ok else "failed", "returncode": 0 if ok else 1, "duration_ms": 2400},
        ]
    }


def _run_metadata(run_id: str, profile: str, model: str, status: str, started: datetime, ended: datetime | None) -> dict[str, Any]:
    result_ok: bool | None
    if status == "ok":
        result_ok = True
    elif status == "failed":
        result_ok = False
    else:
        result_ok = None

    return {
        "run_id": run_id,
        "request_id": f"req-{run_id}",
        "requested_profile": profile,
        "llm": {"model": model},
        "run_started_at": _iso(started),
        "run_completed_at": _iso(ended),
        "result_ok": result_ok,
    }


def _checkpoint(status: str) -> dict[str, Any]:
    if status == "ok":
        cp_status = "completed"
    elif status == "failed":
        cp_status = "failed"
    else:
        cp_status = "running"
    return {
        "status": cp_status,
        "completed_task_ids": ["setup"],
        "failed_task_ids": ["core"] if status == "failed" else [],
        "skipped_task_ids": [],
    }


def _build_specs() -> list[FixtureSpec]:
    return [
        FixtureSpec("showoff_ok_001", "ok", "minimal", "anthropic/claude-opus-4-6", True),
        FixtureSpec("showoff_failed_001", "failed", "enterprise", "openai-codex/gpt-5.3-codex", True),
        FixtureSpec("showoff_running_001", "running", "openclaw-oauth-bridge", "anthropic/claude-opus-4-6", False),
    ]


def generate(root: Path, *, clean: bool = False) -> Path:
    root = root.resolve()
    if clean and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    for i, spec in enumerate(_build_specs()):
        run_dir = root / spec.run_id / ".autodev"
        started = BASE_TIME + timedelta(minutes=i * 11)
        ended = started + timedelta(seconds=25) if spec.ended else None

        _write_json(run_dir / "run_trace.json", _run_trace(spec.run_id, spec.profile, spec.model, started, ended))
        _write_json(run_dir / "task_quality_index.json", _quality(spec.status, spec.profile))
        _write_json(run_dir / "task_final_last_validation.json", _final_validation(spec.status == "ok"))
        _write_json(run_dir / "run_metadata.json", _run_metadata(spec.run_id, spec.profile, spec.model, spec.status, started, ended))
        _write_json(run_dir / "checkpoint.json", _checkpoint(spec.status))

    return root


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate deterministic showoff fixtures")
    parser.add_argument("--root", default="generated_runs", help="output root (default: generated_runs)")
    parser.add_argument("--clean", action="store_true", help="remove output root before regeneration")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    root = generate(Path(args.root), clean=args.clean)
    print(f"[showoff] fixtures generated at: {root}")


if __name__ == "__main__":
    main()
