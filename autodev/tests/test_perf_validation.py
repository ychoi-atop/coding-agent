from __future__ import annotations

from pathlib import Path
import importlib.util
import sys


def _load_perf_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docs" / "ops" / "perf_validation.py"
    spec = importlib.util.spec_from_file_location("perf_validation", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load perf_validation module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


perf_validation = _load_perf_module()


def test_collect_task_perf_rows_from_task_index(tmp_path: Path) -> None:
    autodev_dir = tmp_path / ".autodev"
    autodev_dir.mkdir()
    (autodev_dir / "task_quality_index.json").write_text(
        '''
{
  "tasks": [
    {
      "task_id": "T-001",
      "status": "passed",
      "attempts": 1,
      "soft_failures": 0,
      "hard_failures": 0,
      "attempt_trend": [
        {"duration_ms": 120, "status": "passed", "attempt": 1}
      ]
    },
    {
      "task_id": "T-002",
      "status": "passed",
      "attempts": 1,
      "soft_failures": 0,
      "hard_failures": 0,
      "attempt_trend": [
        {"duration_ms": 180, "status": "passed", "attempt": 1}
      ]
    }
  ]
}
''',
        encoding="utf-8",
    )

    rows = perf_validation.collect_task_perf_rows(tmp_path)
    assert len(rows) == 2
    assert rows[0].task_id == "T-001"
    assert rows[0].duration_ms == 120
    assert rows[1].duration_ms == 180


def test_build_perf_metrics_and_compare_thresholds(tmp_path: Path) -> None:
    rows = [
        perf_validation.TaskPerfRow(
            task_id="T-001",
            attempts=1,
            duration_ms=200,
            status="passed",
            hard_failures=0,
            soft_failures=0,
        ),
        perf_validation.TaskPerfRow(
            task_id="T-002",
            attempts=2,
            duration_ms=100,
            status="passed",
            hard_failures=0,
            soft_failures=0,
        ),
    ]
    payload = perf_validation.summarize_payload(tmp_path, rows)
    assert payload["metrics"]["task_count"] == 2
    assert payload["metrics"]["total_validation_ms"] == 300

    previous = {
        "metrics": {
            "total_validation_ms": 250,
            "max_task_validation_ms": 180,
        }
    }

    ok, compare_payload = perf_validation.compare_perf(
        payload,
        previous,
        max_ratio=0.3,
        max_abs_ms=80,
    )

    assert ok is True
    assert compare_payload["delta_total_ms"] == 50
    assert compare_payload["checks"]["total_abs_ok"] is True

    strict_ok, strict_compare = perf_validation.compare_perf(
        payload,
        previous,
        max_ratio=0.1,
        max_abs_ms=30,
    )

    assert strict_ok is False
    assert strict_compare["checks"]["total_ratio_ok"] is False
    assert strict_compare["checks"]["total_abs_ok"] is False


def test_run_perf_check_writes_and_loads_previous(tmp_path: Path) -> None:
    run_dir = tmp_path / "generated_repo"
    run_autodev = run_dir / ".autodev"
    run_autodev.mkdir(parents=True)

    (run_autodev / "task_quality_index.json").write_text(
        '{"tasks": [{"task_id": "T-001", "status": "passed", "attempts": 1, "soft_failures": 0, "hard_failures": 0, "attempt_trend": [{"duration_ms": 100}]}]}',
        encoding="utf-8",
    )

    out = run_dir / ".autodev" / "perf.json"
    code_first, payload_first, _ = perf_validation.run_perf_check(
        run_dir=run_dir,
        out_path=out,
        max_ratio=None,
        max_abs_ms=None,
        require_data=True,
        enforce=False,
        compare=True,
    )

    assert code_first == 0
    assert out.exists()
    baseline = out.read_text(encoding="utf-8")

    (run_autodev / "task_quality_index.json").write_text(
        '{"tasks": [{"task_id": "T-001", "status": "passed", "attempts": 1, "soft_failures": 0, "hard_failures": 0, "attempt_trend": [{"duration_ms": 220}]}]}',
        encoding="utf-8",
    )

    code_second, payload_second, compare_payload = perf_validation.run_perf_check(
        run_dir=run_dir,
        out_path=out,
        max_ratio=0.1,
        max_abs_ms=50,
        require_data=True,
        enforce=True,
        compare=True,
    )

    assert payload_second["metrics"]["total_validation_ms"] == 220
    assert code_second == 1
    assert compare_payload is not None
    assert compare_payload["delta_total_ms"] == 120
    assert baseline != out.read_text(encoding="utf-8")
