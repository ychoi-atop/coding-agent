from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "status_board_automation.py"


def _load_module():
    script_path = _script_path()
    spec = importlib.util.spec_from_file_location("status_board_automation", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(_script_path()), *args]
    return subprocess.run(cmd, capture_output=True, text=True)


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

- **AV2:** ✅ Closed (`AV2-001` ~ `AV2-014`)
- **AV3:** ✅ Closed (`AV3-001` ~ `AV3-013`)
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
    (docs_root / "AUTONOMOUS_V4_WAVE_CLOSURE.md").write_text(
        """# Autonomous v4 Wave Closure

Status: 🚧 Open (kickoff active; closure pending)

## Completed tickets (`AV4-001` ~ `AV4-014`)

- TODO
""",
        encoding="utf-8",
    )


def test_event_registry_valid_default_passes_schema_validation() -> None:
    mod = _load_module()
    assert mod.validate_event_registry(mod.EVENT_REGISTRY) == []

    expected_events = {
        "av4.kickoff.started",
        "av4.execution.in_progress",
        "av4.stabilization.started",
        "av4.closed",
    }
    assert set(mod.CANONICAL_EVENT_MAP) == expected_events


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


def test_apply_and_drift_check_support_non_kickoff_event(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    changed = mod.apply_event(
        "av4.execution.in_progress",
        docs_root=docs_root,
        timestamp="2026-03-09 00:01 KST (Asia/Seoul)",
    )
    assert {p.name for p in changed} == {
        "STATUS_BOARD_CURRENT.md",
        "PLAN_NEXT_WEEK.md",
        "BACKLOG_NEXT_WEEK.md",
        "AUTONOMOUS_V4_WAVE_CLOSURE.md",
    }

    status_text = (docs_root / "STATUS_BOARD_CURRENT.md").read_text(encoding="utf-8")
    plan_text = (docs_root / "PLAN_NEXT_WEEK.md").read_text(encoding="utf-8")
    backlog_text = (docs_root / "BACKLOG_NEXT_WEEK.md").read_text(encoding="utf-8")
    closure_text = (docs_root / "AUTONOMOUS_V4_WAVE_CLOSURE.md").read_text(encoding="utf-8")

    assert "- **Mode:** AV4 Execution" in status_text
    assert "# PLAN — Next Wave (AV4 Execution In Progress)" in plan_text
    assert "- AV4 execution: 🏗️ in progress" in backlog_text
    assert "Status: 🏗️ Open (execution in progress)" in closure_text

    assert mod.drift_check_event("av4.execution.in_progress", docs_root=docs_root) == []

    changed_again = mod.apply_event(
        "av4.execution.in_progress",
        docs_root=docs_root,
        timestamp="2026-03-09 00:01 KST (Asia/Seoul)",
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


def test_invalid_registry_fails_with_actionable_diagnostics() -> None:
    mod = _load_module()

    invalid_registry = [
        {
            "event_id": "",
            "description": "",
            "expected_doc_transitions": ["STATUS_BOARD_CURRENT.md"],
            "spec": {},
        }
    ]

    errors = mod.validate_event_registry(invalid_registry)
    assert any("event_id must be a non-empty string" in err for err in errors)
    assert any("description must be a non-empty string" in err for err in errors)
    assert any("expected_doc_transitions must exactly match" in err for err in errors)
    assert any("spec.mode must be a non-empty string" in err for err in errors)

    with pytest.raises(ValueError, match="invalid status-hook event registry"):
        mod._build_event_map_from_registry(invalid_registry)


def test_apply_event_unknown_event_raises_with_known_events(tmp_path: Path) -> None:
    mod = _load_module()
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)

    with pytest.raises(ValueError, match=r"unknown event 'av4\.unknown'\. Known events: "):
        mod.apply_event("av4.unknown", docs_root=docs_root)


def test_cli_appends_audit_for_apply_and_drift_check(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"

    apply_result = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--timestamp",
        "2026-03-09 09:00 KST (Asia/Seoul)",
        "--audit-log",
        str(audit_path),
    )
    assert apply_result.returncode == 0, apply_result.stderr + apply_result.stdout

    drift_result = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--drift-check",
        "--audit-log",
        str(audit_path),
    )
    assert drift_result.returncode == 0, drift_result.stderr + drift_result.stdout

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["mode"] == "apply"
    assert rows[0]["hook_event_id"] == "av4.execution.in_progress"
    assert rows[0]["outcome"] in {"updated", "noop"}
    assert set(Path(p).name for p in rows[0]["target_docs"]) == {
        "STATUS_BOARD_CURRENT.md",
        "PLAN_NEXT_WEEK.md",
        "BACKLOG_NEXT_WEEK.md",
        "AUTONOMOUS_V4_WAVE_CLOSURE.md",
    }
    assert rows[1]["mode"] == "drift-check"
    assert rows[1]["hook_event_id"] == "av4.execution.in_progress"
    assert rows[1]["outcome"] == "pass"


def test_replay_is_dry_run_by_default(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"

    first = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--timestamp",
        "2026-03-09 10:00 KST (Asia/Seoul)",
        "--audit-log",
        str(audit_path),
    )
    assert first.returncode == 0, first.stderr + first.stdout

    baseline_status = (docs_root / "STATUS_BOARD_CURRENT.md").read_text(encoding="utf-8")
    replay = _run_cli(
        "--replay",
        "1",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
    )
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert "[DRY-RUN] Replay" in replay.stdout
    assert (docs_root / "STATUS_BOARD_CURRENT.md").read_text(encoding="utf-8") == baseline_status

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["mode"] == "replay-dry-run"
    assert rows[-1]["diagnostics"]["replay_source_index"] == 1


def test_replay_apply_requires_explicit_flag(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"

    first = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--timestamp",
        "2026-03-09 11:00 KST (Asia/Seoul)",
        "--audit-log",
        str(audit_path),
    )
    assert first.returncode == 0, first.stderr + first.stdout

    # Force drift so replay-apply has concrete work.
    plan_path = docs_root / "PLAN_NEXT_WEEK.md"
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("AV4 Execution In Progress", "AV4 Drifted"),
        encoding="utf-8",
    )

    replay_apply = _run_cli(
        "--replay",
        "1",
        "--apply",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
    )
    assert replay_apply.returncode == 0, replay_apply.stderr + replay_apply.stdout
    assert "[PASS] Replay apply" in replay_apply.stdout

    repaired_plan = plan_path.read_text(encoding="utf-8")
    assert "AV4 Execution In Progress" in repaired_plan

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["mode"] == "replay-apply"


def test_replay_invalid_entry_handling_is_clear(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"

    missing = _run_cli(
        "--replay",
        "missing-entry-id",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
    )
    assert missing.returncode == 1
    assert "status-hook audit trail is empty" in missing.stdout

    _run_cli(
        "av4.kickoff.started",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
    )
    invalid = _run_cli(
        "--replay",
        "999",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
    )
    assert invalid.returncode == 1
    assert "replay index out of range" in invalid.stdout


def test_apply_acquires_and_releases_write_lock(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"
    lock_path = tmp_path / "status-hook.lock"

    result = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
        "--lock-file",
        str(lock_path),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[LOCK] Acquired" in result.stdout
    assert "[LOCK] Released" in result.stdout
    assert not lock_path.exists()

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    last = rows[-1]
    assert last["mode"] == "apply"
    assert last["outcome"] in {"updated", "noop"}
    assert last["diagnostics"]["lock"]["acquire"]["status"] == "acquired"
    assert last["diagnostics"]["lock"]["release"]["status"] in {"released", "already_missing"}


def test_second_concurrent_invocation_is_blocked(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"
    lock_path = tmp_path / "status-hook.lock"

    lock_path.write_text(
        json.dumps(
            {
                "lock_token": "existing",
                "pid": 99999,
                "acquired_at_epoch": time.time(),
                "acquired_at_kst": "2026-03-09 00:00 KST (Asia/Seoul)",
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli(
        "av4.kickoff.started",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
        "--lock-file",
        str(lock_path),
        "--lock-stale-seconds",
        "600",
    )
    assert result.returncode == 1
    assert "blocked by write lock" in result.stdout

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    last = rows[-1]
    assert last["outcome"] == "blocked_by_lock"
    assert last["diagnostics"]["lock"]["status"] == "blocked"


def test_stale_lock_requires_force_override(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"
    lock_path = tmp_path / "status-hook.lock"

    lock_path.write_text(
        json.dumps(
            {
                "lock_token": "stale-existing",
                "pid": 777,
                "acquired_at_epoch": time.time() - 3600,
                "acquired_at_kst": "2026-03-08 20:00 KST (Asia/Seoul)",
            }
        ),
        encoding="utf-8",
    )

    blocked = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
        "--lock-file",
        str(lock_path),
        "--lock-stale-seconds",
        "5",
    )
    assert blocked.returncode == 1
    assert "appears stale" in blocked.stdout

    forced = _run_cli(
        "av4.execution.in_progress",
        "--docs-root",
        str(docs_root),
        "--audit-log",
        str(audit_path),
        "--lock-file",
        str(lock_path),
        "--lock-stale-seconds",
        "5",
        "--force-lock",
    )
    assert forced.returncode == 0, forced.stderr + forced.stdout
    assert "[LOCK] Acquired" in forced.stdout
    assert "[LOCK] Released" in forced.stdout

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-2]["outcome"] == "blocked_by_lock"
    assert rows[-1]["diagnostics"]["lock"]["acquire"]["stale_override"] is True


def test_drift_check_mode_is_read_only_and_ignores_write_lock(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    _seed_docs(docs_root)
    audit_path = tmp_path / "status-hook-audit.jsonl"
    lock_path = tmp_path / "status-hook.lock"

    # Simulate another writer holding the lock; drift-check should still run read-only.
    original_lock = {
        "lock_token": "held-by-writer",
        "pid": 4242,
        "acquired_at_epoch": time.time(),
        "acquired_at_kst": "2026-03-09 01:00 KST (Asia/Seoul)",
    }
    lock_path.write_text(json.dumps(original_lock), encoding="utf-8")

    result = _run_cli(
        "av4.kickoff.started",
        "--docs-root",
        str(docs_root),
        "--drift-check",
        "--audit-log",
        str(audit_path),
        "--lock-file",
        str(lock_path),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "[PASS] Status hook drift check passed" in result.stdout
    assert "[LOCK]" not in result.stdout

    assert json.loads(lock_path.read_text(encoding="utf-8")) == original_lock
