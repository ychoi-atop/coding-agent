from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "render_av5_kickoff_smoke_evidence_index.py"


def _load_module():
    script_path = _script_path()
    spec = importlib.util.spec_from_file_location("render_av5_kickoff_smoke_evidence_index", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mk_run(root: Path, stamp: str, *, ok: bool = True) -> None:
    run = root / stamp
    run.mkdir(parents=True, exist_ok=True)
    (run / "result.json").write_text(json.dumps({"ok": ok}), encoding="utf-8")


def test_collect_rows_returns_latest_entries(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()

    smoke_a = tmp_path / "autonomous"
    smoke_b = tmp_path / "retry"
    smoke_c = tmp_path / "taxonomy"

    _mk_run(smoke_a, "20260309-090000", ok=True)
    _mk_run(smoke_b, "20260309-090100", ok=False)
    _mk_run(smoke_c, "20260309-090200", ok=True)

    monkeypatch.setattr(
        mod,
        "SOURCES",
        (
            mod.SmokeSource(ticket="AV2-013", check="a", artifacts_dir=smoke_a),
            mod.SmokeSource(ticket="AV5-004", check="b", artifacts_dir=smoke_b),
            mod.SmokeSource(ticket="AV5-008", check="c", artifacts_dir=smoke_c),
        ),
    )
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)

    now = datetime(2026, 3, 9, 9, 10, 0, tzinfo=timezone.utc)
    rows = mod.collect_rows(now=now, freshness_hours=2)

    assert [r["source"] for r in rows] == ["AV2-013", "AV5-004", "AV5-008"]
    assert rows[1]["outcome"] == "❌ FAIL"
    assert rows[0]["artifact"].endswith("result.json")


def test_collect_rows_fails_when_stale(monkeypatch, tmp_path: Path) -> None:
    mod = _load_module()

    smoke = tmp_path / "autonomous"
    _mk_run(smoke, "20260301-000000", ok=True)

    monkeypatch.setattr(mod, "SOURCES", (mod.SmokeSource(ticket="AV2-013", check="a", artifacts_dir=smoke),))

    now = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)

    try:
        mod.collect_rows(now=now, freshness_hours=24)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "stale smoke evidence" in str(exc)


def test_render_markdown_contains_required_table_columns() -> None:
    mod = _load_module()

    md = mod.render_markdown(
        rows=[
            {
                "timestamp_kst": "2026-03-09 18:00:00 KST",
                "run_time_utc": "2026-03-09T09:00:00+00:00",
                "source": "AV2-013",
                "check": "autonomous_e2e_smoke",
                "outcome": "✅ PASS",
                "artifact": "artifacts/autonomous-e2e-smoke/20260309-090000/result.json",
            }
        ],
    )

    assert "| Timestamp (KST) | Source | Check | Outcome | Artifact |" in md
    assert "`AV2-013`" in md
    assert "✅ PASS" in md
