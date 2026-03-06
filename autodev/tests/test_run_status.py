from __future__ import annotations

from autodev.run_status import normalize_run_status


def test_normalize_run_status_prefers_metadata_result_ok() -> None:
    assert normalize_run_status(metadata={"result_ok": True}, checkpoint={"status": "failed"}) == "ok"
    assert normalize_run_status(metadata={"result_ok": False}, checkpoint={"status": "completed"}) == "failed"


def test_normalize_run_status_uses_quality_before_checkpoint() -> None:
    status = normalize_run_status(
        quality_index={"final": {"status": "failed"}},
        checkpoint={"status": "running"},
    )
    assert status == "failed"


def test_normalize_run_status_aliases_and_default() -> None:
    assert normalize_run_status(checkpoint={"status": "completed"}) == "ok"
    assert normalize_run_status(checkpoint={"status": "in_progress"}) == "running"
    assert normalize_run_status(checkpoint={"status": "something_new"}) == "unknown"
    assert normalize_run_status(default="running") == "running"
