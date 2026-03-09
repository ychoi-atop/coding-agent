from __future__ import annotations

from pathlib import Path


def test_makefile_default_status_hook_event_is_auto_detect() -> None:
    makefile_path = Path(__file__).resolve().parents[2] / "Makefile"
    makefile = makefile_path.read_text(encoding="utf-8")

    assert "STATUS_HOOK_EVENT ?= auto" in makefile
    assert "scripts/status_board_automation.py --detect-event" in makefile
