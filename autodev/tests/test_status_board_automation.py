from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "status_board_automation.py"
    spec = importlib.util.spec_from_file_location("status_board_automation", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_docs(docs_root: Path) -> None:
    docs_root.mkdir(parents=True, exist_ok=True)
    (docs_root / "STATUS_BOARD_CURRENT.md").write_text(
        """# STATUS BOARD — CURRENT

Status timestamp: 2026-01-01 00:00 KST (Asia/Seoul)

## Current phase

- **Mode:** AV4 Kickoff
- **Scope:** AV4 wave planning + kickoff execution start
- **State:** AV3 closed on `main`; AV4 kickoff package started

## Wave status snapshot

- **AV4:** 🚧 Kickoff started (plan + backlog published)
""",
        encoding="utf-8",
    )
    (docs_root / "PLAN_NEXT_WEEK.md").write_text(
        """# PLAN — Next Wave (AV4 Kickoff Active)

## Current state snapshot
- AV4 kickoff package is now active (`docs/AUTONOMOUS_V4_WAVE_PLAN.md`, `docs/AUTONOMOUS_V4_BACKLOG.md`).
""",
        encoding="utf-8",
    )
    (docs_root / "BACKLOG_NEXT_WEEK.md").write_text(
        """# BACKLOG — Next Wave (AV4 Kickoff Queue)

## Wave baseline
- AV4 kickoff: 🚧 started
""",
        encoding="utf-8",
    )


def test_event_mapping_contains_av4_kickoff_started() -> None:
    mod = _load_module()
    assert "av4.kickoff.started" in mod.CANONICAL_EVENT_MAP


def test_apply_event_updates_docs_and_is_idempotent_with_fixed_timestamp(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    changed = mod.apply_event("av4.kickoff.started", docs_root=docs_root, timestamp="2026-03-08 23:30 KST (Asia/Seoul)")
    assert {p.name for p in changed} == {"STATUS_BOARD_CURRENT.md"}

    status_text = (docs_root / "STATUS_BOARD_CURRENT.md").read_text(encoding="utf-8")
    assert "Status timestamp: 2026-03-08 23:30 KST (Asia/Seoul)" in status_text

    # Same event + fixed timestamp should be no-op.
    changed_again = mod.apply_event(
        "av4.kickoff.started",
        docs_root=docs_root,
        timestamp="2026-03-08 23:30 KST (Asia/Seoul)",
    )
    assert changed_again == []


def test_drift_check_passes_when_docs_match_with_existing_timestamp(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    # Different timestamp should still pass in drift-check mode when canonical fields match.
    drifted = mod.drift_check_event("av4.kickoff.started", docs_root=docs_root)
    assert drifted == []


def test_drift_check_fails_and_does_not_write_docs(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    plan_path = docs_root / "PLAN_NEXT_WEEK.md"
    original_plan = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(original_plan.replace("AV4 Kickoff Active", "AV4 Drifted"), encoding="utf-8")

    drifted = mod.drift_check_event("av4.kickoff.started", docs_root=docs_root)
    assert [p.name for p in drifted] == ["PLAN_NEXT_WEEK.md"]
    assert plan_path.read_text(encoding="utf-8") == original_plan.replace("AV4 Kickoff Active", "AV4 Drifted")


def test_apply_event_unknown_event_raises(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    with pytest.raises(ValueError, match="unknown event"):
        mod.apply_event("av4.unknown", docs_root=docs_root)
