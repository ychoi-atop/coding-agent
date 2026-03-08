from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_mode as autonomous_mode  # noqa: E402
from autodev.autonomous_issue_export import (  # noqa: E402
    AUTONOMOUS_ISSUE_EXPORT_JSON,
    export_github_issue,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_ticket_draft(run_dir: Path) -> None:
    _write_json(
        run_dir / ".autodev" / "autonomous_ticket_draft.json",
        {
            "title": "[AutoDev][high] tests.min_pass_rate_not_met on run-issue",
            "severity": "high",
            "owner_team": "Feature Engineering",
            "target_sla": "4h",
            "status": "failed",
            "failure_reason": "autonomous_guard_stop",
            "typed_codes": ["tests.min_pass_rate_not_met"],
            "repro_steps": ["Open run artifacts", "Inspect failing tests"],
            "evidence": [{"label": "report", "path": ".autodev/autonomous_report.json"}],
            "suggested_next_actions": ["Fix deterministic test failures first."],
        },
    )


def test_issue_export_dry_run_generates_payload_and_persists_summary_metadata(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-issue-dry"
    _seed_ticket_draft(run_dir)
    _write_json(run_dir / ".autodev" / "autonomous_report.json", {"ok": False, "run_id": "run-issue"})

    autonomous_mode.cli(
        [
            "issue-export",
            "--run-dir",
            str(run_dir),
            "--repo",
            "owner/repo",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["dry_run"] is True
    assert payload["status"] == "dry_run"
    assert payload["repo"] == "owner/repo"
    assert payload["payload"]["title"].startswith("[AutoDev][high]")
    assert "gh issue create --repo owner/repo" in payload["command_preview"]

    export_artifact = run_dir / AUTONOMOUS_ISSUE_EXPORT_JSON
    stored = json.loads(export_artifact.read_text(encoding="utf-8"))
    assert len(stored["attempts"]) == 1
    assert stored["latest"]["status"] == "dry_run"

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))
    assert summary["issue_export_status"] == "ok"
    assert summary["issue_export_latest_status"] == "dry_run"
    assert summary["issue_export"]["latest"]["repo"] == "owner/repo"


def test_issue_export_live_mode_reports_clear_guard_when_gh_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-issue-no-gh"
    _seed_ticket_draft(run_dir)

    result = export_github_issue(
        run_dir=run_dir,
        repo="owner/repo",
        dry_run=False,
        which=lambda _: None,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert any(item.get("code") == "issue_export.gh_cli_missing" for item in result["diagnostics"])


def test_issue_export_falls_back_when_ticket_draft_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-issue-fallback"
    _write_json(run_dir / ".autodev" / "autonomous_report.json", {"ok": False, "run_id": "run-fallback"})

    result = export_github_issue(run_dir=run_dir, repo="owner/repo", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["payload"]["title"]
    assert "AutoDev autonomous failure export" in result["payload"]["body"]
    assert any(
        item.get("code") == "issue_export.ticket_draft_missing_generated_fallback"
        for item in result["diagnostics"]
    )
