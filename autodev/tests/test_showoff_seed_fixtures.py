from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_seed_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "showoff_seed_fixtures.py"
    spec = spec_from_file_location("showoff_seed_fixtures", script_path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_seed_fixtures_generate_expected_statuses_and_artifacts(tmp_path: Path) -> None:
    mod = _load_seed_module()
    root = mod.generate(tmp_path / "generated_runs", clean=True)

    runs = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert runs == ["showoff_failed_001", "showoff_ok_001", "showoff_running_001"]

    statuses = {}
    for run_id in runs:
        ad = root / run_id / ".autodev"
        assert (ad / "run_trace.json").is_file()
        assert (ad / "task_quality_index.json").is_file()
        assert (ad / "task_final_last_validation.json").is_file()
        assert (ad / "run_metadata.json").is_file()
        assert (ad / "checkpoint.json").is_file()

        metadata = _read_json(ad / "run_metadata.json")
        checkpoint = _read_json(ad / "checkpoint.json")
        statuses[run_id] = (metadata.get("result_ok"), checkpoint.get("status"))

    assert statuses["showoff_ok_001"] == (True, "completed")
    assert statuses["showoff_failed_001"] == (False, "failed")
    assert statuses["showoff_running_001"] == (None, "running")


def test_seed_fixtures_are_deterministic(tmp_path: Path) -> None:
    mod = _load_seed_module()
    root = tmp_path / "generated_runs"

    mod.generate(root, clean=True)
    first = (root / "showoff_ok_001" / ".autodev" / "run_trace.json").read_text(encoding="utf-8")

    mod.generate(root, clean=True)
    second = (root / "showoff_ok_001" / ".autodev" / "run_trace.json").read_text(encoding="utf-8")

    assert first == second
