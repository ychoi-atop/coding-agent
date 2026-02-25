#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable


DEFAULT_RUN_DIR = Path("generated_repo")


@dataclass(frozen=True)
class TaskPerfRow:
    task_id: str
    attempts: int
    duration_ms: int
    status: str
    hard_failures: int
    soft_failures: int


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _collect_from_task_index(task_index: Dict[str, Any]) -> list[TaskPerfRow]:
    rows: list[TaskPerfRow] = []
    for task in task_index.get("tasks", []):
        task_id = str(task.get("task_id", "unknown"))
        status = str(task.get("status", "unknown"))
        attempts = _safe_int(task.get("attempts", 0))
        soft_failures = _safe_int(task.get("soft_failures", 0))
        hard_failures = _safe_int(task.get("hard_failures", 0))

        attempt_trend = task.get("attempt_trend", [])
        last_duration = 0
        if isinstance(attempt_trend, list) and attempt_trend:
            last = attempt_trend[-1]
            if isinstance(last, dict):
                last_duration = _safe_int(last.get("duration_ms", 0))

        rows.append(
            TaskPerfRow(
                task_id=task_id,
                attempts=attempts,
                duration_ms=last_duration,
                status=status,
                hard_failures=hard_failures,
                soft_failures=soft_failures,
            )
        )

    # Fallback: some interrupted/partial runs only persist final validator rows.
    # Convert final validation entries into perf rows so strict mode can still
    # assert that measurable validation data exists.
    if not rows:
        final = task_index.get("final", {})
        validations = final.get("validations", []) if isinstance(final, dict) else []
        if isinstance(validations, list):
            for idx, row in enumerate(validations, start=1):
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or f"validator_{idx}")
                ok = bool(row.get("ok"))
                status = "passed" if ok else "failed"
                rows.append(
                    TaskPerfRow(
                        task_id=f"final:{name}",
                        attempts=1,
                        duration_ms=_safe_int(row.get("duration_ms", 0)),
                        status=status,
                        hard_failures=0 if ok else 1,
                        soft_failures=0,
                    )
                )

    return rows


def _collect_from_task_quality_files(run_dir: Path) -> list[TaskPerfRow]:
    rows: list[TaskPerfRow] = []
    for candidate in sorted((run_dir / ".autodev").glob("task_*_quality.json")):
        payload = _read_json(candidate)
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id", candidate.stem.replace("task_", "", 1).replace("_quality", "")))
        attempts = payload.get("attempts", []) if isinstance(payload.get("attempts"), list) else []
        last_attempt = attempts[-1] if attempts else {}
        if not isinstance(last_attempt, dict):
            last_attempt = {}

        attempts_count = _safe_int(payload.get("attempts_count", len(attempts)))
        duration_ms = _safe_int(last_attempt.get("duration_ms", 0))
        status = str(payload.get("status", "unknown"))
        hard_failures = _safe_int(last_attempt.get("hard_failures", 0))
        soft_failures = _safe_int(last_attempt.get("soft_failures", 0))

        rows.append(
            TaskPerfRow(
                task_id=task_id,
                attempts=attempts_count,
                duration_ms=duration_ms,
                status=status,
                hard_failures=hard_failures,
                soft_failures=soft_failures,
            )
        )

    return rows


def collect_task_perf_rows(run_dir: Path) -> list[TaskPerfRow]:
    run_dir = run_dir.resolve()
    task_index = _read_json(run_dir / ".autodev" / "task_quality_index.json")
    rows = _collect_from_task_index(task_index) if isinstance(task_index, dict) else []
    if rows:
        return rows

    rows = _collect_from_task_quality_files(run_dir)
    if rows:
        return rows

    return []


def build_perf_metrics(rows: list[TaskPerfRow]) -> Dict[str, Any]:
    durations = [r.duration_ms for r in rows]
    return {
        "task_count": len(rows),
        "passed_tasks": sum(1 for r in rows if r.status == "passed"),
        "failed_tasks": sum(1 for r in rows if r.status != "passed"),
        "total_validation_ms": sum(durations),
        "max_task_validation_ms": max(durations) if durations else 0,
        "p95_task_validation_ms": int(
            sorted(durations)[max(0, int((len(durations) - 1) * 0.95))] if durations else 0
        ),
        "median_task_validation_ms": int(median(durations)) if durations else 0,
        "rows": [
            {
                "task_id": r.task_id,
                "attempts": r.attempts,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "hard_failures": r.hard_failures,
                "soft_failures": r.soft_failures,
            }
            for r in rows
        ],
    }


def summarize_payload(run_dir: Path, rows: list[TaskPerfRow]) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "schema_version": "1.0",
        "metrics": build_perf_metrics(rows),
    }


def _build_compare_payload(
    current_total: int,
    current_max: int,
    previous: Dict[str, Any],
    max_ratio: float | None,
    max_abs_ms: int | None,
) -> Dict[str, Any]:
    prev_metrics = previous.get("metrics", {}) if isinstance(previous, dict) else {}
    prev_total = _safe_int(prev_metrics.get("total_validation_ms", 0))
    prev_max = _safe_int(prev_metrics.get("max_task_validation_ms", 0))

    delta_total = current_total - prev_total
    delta_max = current_max - prev_max
    ratio_total = (delta_total / prev_total) if prev_total > 0 else 0.0

    max_delta_ratio = ((delta_max / prev_max) if prev_max > 0 else 0.0)

    total_ratio_check = True
    max_ratio_check = True
    if max_ratio is not None and prev_total > 0:
        total_ratio_check = ratio_total <= max_ratio
    if max_ratio is not None and prev_max > 0:
        max_ratio_check = max_delta_ratio <= max_ratio

    total_abs_check = True
    max_abs_check = True
    if max_abs_ms is not None:
        total_abs_check = delta_total <= max_abs_ms
        max_abs_check = delta_max <= max_abs_ms

    return {
        "available": bool(previous),
        "previous_total_validation_ms": prev_total,
        "previous_max_task_validation_ms": prev_max,
        "delta_total_ms": delta_total,
        "delta_max_task_ms": delta_max,
        "total_ratio": ratio_total,
        "max_ratio": max_delta_ratio,
        "thresholds": {
            "max_ratio": max_ratio,
            "max_abs_ms": max_abs_ms,
        },
        "checks": {
            "total_ratio_ok": total_ratio_check,
            "max_ratio_ok": max_ratio_check,
            "total_abs_ok": total_abs_check,
            "max_abs_ok": max_abs_check,
        },
    }


def compare_perf(
    current: Dict[str, Any],
    previous: Dict[str, Any] | None,
    max_ratio: float | None,
    max_abs_ms: int | None,
) -> tuple[bool, Dict[str, Any]]:
    if not previous:
        return True, {"available": False}

    metrics = current.get("metrics", {})
    current_total = _safe_int(metrics.get("total_validation_ms", 0))
    current_max = _safe_int(metrics.get("max_task_validation_ms", 0))
    payload = _build_compare_payload(current_total, current_max, previous, max_ratio, max_abs_ms)

    checks = payload.get("checks", {})
    return (
        all(bool(checks.get(k)) for k in checks),
        payload,
    )


def _dump(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and compare lightweight perf metrics for generated runs.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Path to generated run directory.")
    parser.add_argument("--out", default=None, help="Output perf JSON path. Defaults to <run-dir>/.autodev/perf.json")
    parser.add_argument(
        "--max-regression-ratio",
        type=float,
        default=None,
        help="Fail if total/max task regressions exceed ratio (e.g., 0.20 = 20%).",
    )
    parser.add_argument(
        "--max-regression-ms",
        type=int,
        default=None,
        help="Fail if total or max task regresses by more than this many ms.",
    )
    parser.add_argument(
        "--require-data",
        action="store_true",
        help="Require at least one parsed validation task row.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Collect and write perf output without comparing against prior output.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def run_perf_check(
    run_dir: Path,
    out_path: Path,
    max_ratio: float | None,
    max_abs_ms: int | None,
    require_data: bool,
    enforce: bool,
    compare: bool,
) -> tuple[int, Dict[str, Any], Dict[str, Any] | None]:
    rows = collect_task_perf_rows(run_dir)
    if require_data and not rows:
        raise RuntimeError(f"No validation task data found in {run_dir}")

    payload = summarize_payload(run_dir=run_dir, rows=rows)
    previous: Dict[str, Any] | None = None
    if compare and out_path.exists():
        previous_data = _read_json(out_path)
        if isinstance(previous_data, dict):
            previous = previous_data

    compare_result: Dict[str, Any] | None = None
    if compare:
        ok, compare_payload = compare_perf(payload, previous, max_ratio=max_ratio, max_abs_ms=max_abs_ms)
        compare_result = compare_payload
        payload["compare"] = compare_payload
        if enforce and not ok:
            _dump(out_path, payload)
            return 1, payload, compare_result

    _dump(out_path, payload)
    return 0, payload, compare_result


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    run_dir = Path(args.run_dir)
    out_path = Path(args.out) if args.out else (run_dir / ".autodev" / "perf.json")

    code, payload, compare_result = run_perf_check(
        run_dir=run_dir,
        out_path=out_path,
        max_ratio=args.max_regression_ratio,
        max_abs_ms=args.max_regression_ms,
        require_data=args.require_data,
        enforce=bool(args.max_regression_ratio or args.max_regression_ms),
        compare=not args.no_compare,
    )

    print(f"Perf artifact: {out_path}")
    metrics = payload.get("metrics", {})
    print(
        f"Perf totals: tasks={metrics.get('task_count', 0)} total={metrics.get('total_validation_ms', 0)}ms "
        f"max={metrics.get('max_task_validation_ms', 0)}ms"
    )

    if compare_result is not None and compare_result.get("available"):
        print(
            f"Perf compare: delta_total={compare_result.get('delta_total_ms', 0)}ms "
            f"delta_max={compare_result.get('delta_max_task_ms', 0)}ms"
        )

    if code != 0:
        print("Perf regression thresholds exceeded.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
