from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from urllib import error as urllib_error

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import autodev.autonomous_incident_send as incident_send  # noqa: E402
import autodev.autonomous_mode as autonomous_mode  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sample_packet() -> dict:
    return {
        "schema_version": "av3-005-v1",
        "status": "failed",
        "run_summary": {
            "run_id": "run-incident-send",
            "request_id": "req-incident-send",
            "profile": "minimal",
            "failure_reason": "preflight_failed",
            "iterations_total": 0,
            "iterations_failed": 0,
            "completed_at": "2026-03-08T11:11:00Z",
        },
        "failure_codes": {
            "typed_codes": ["autonomous_preflight.path_blocked"],
            "root_cause_codes": ["autonomous_preflight.path_blocked"],
        },
        "incident_routing": {
            "primary": {
                "owner_team": "Platform Operations",
                "severity": "medium",
                "target_sla": "8h",
                "escalation_class": "run_configuration",
            }
        },
        "reproduction": {
            "run_dir": "/tmp/generated_runs/run-incident-send",
            "artifact_paths": {
                "state": ".autodev/autonomous_state.json",
                "report_json": ".autodev/autonomous_report.json",
                "incident_packet": ".autodev/autonomous_incident_packet.json",
            },
        },
        "operator_guidance": {
            "playbook": "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md",
            "top_actions": [
                {
                    "code": "autonomous_preflight.path_blocked",
                    "title": "Preflight failed: blocked path",
                    "action": "Move inputs out of blocked paths",
                    "playbook_url": "docs/AUTONOMOUS_FAILURE_PLAYBOOK.md#preflight-failures",
                }
            ],
        },
        "generated_at": "2026-03-08T11:11:01Z",
    }


def test_incident_send_cli_dry_run_path_persists_attempt(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run-dry"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    autonomous_mode.cli([
        "incident-send",
        "--run-dir",
        str(run_dir),
        "--target",
        "stdout",
        "--dry-run",
        "true",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["attempts"][0]["status"] == "dry_run"

    persisted = json.loads((run_dir / ".autodev" / "autonomous_incident_send.json").read_text(encoding="utf-8"))
    assert persisted["latest"]["dry_run"] is True
    assert persisted["latest"]["attempts"][0]["target"] == "stdout"


def test_autonomous_failure_path_invokes_enabled_incident_send_with_mock_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: test-key
  model: fake-model
profiles:
  minimal:
    validators:
      - pytest
    template_candidates:
      - python_fastapi
run:
  autonomous:
    max_iterations: 1
    time_budget_sec: 60
    incident_send:
      enabled: true
      dry_run: false
      targets:
        - mock
""",
        encoding="utf-8",
    )
    prd = tmp_path / "prd.md"
    prd.write_text("# goal\n\nship it", encoding="utf-8")
    out_root = tmp_path / "runs"

    calls: list[dict] = []

    def _mock_target(packet: dict, rendered: str, dry_run: bool, context: dict) -> dict:
        calls.append({"packet": packet, "rendered": rendered, "dry_run": dry_run, "context": context})
        return {"mock": True}

    monkeypatch.setitem(incident_send._INCIDENT_SEND_TARGETS, "mock", _mock_target)

    with pytest.raises(SystemExit) as exc:
        autonomous_mode.cli(
            [
                "start",
                "--prd",
                str(prd),
                "--out",
                str(out_root),
                "--config",
                str(cfg),
                "--profile",
                "minimal",
                "--workspace-allowlist",
                str(tmp_path),
                "--blocked-paths",
                str(tmp_path),
            ]
        )

    assert exc.value.code == 1
    assert len(calls) == 1
    assert calls[0]["dry_run"] is False

    run_dir = sorted(out_root.iterdir())[0]
    persisted = json.loads((run_dir / ".autodev" / "autonomous_incident_send.json").read_text(encoding="utf-8"))
    latest = persisted["latest"]
    assert latest["ok"] is True
    assert latest["dry_run"] is False
    assert latest["attempts"][0]["status"] == "sent"
    assert latest["attempts"][0]["target"] == "mock"


def test_incident_send_cli_handles_missing_packet(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-missing"
    run_dir.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        autonomous_mode.cli(["incident-send", "--run-dir", str(run_dir)])

    message = str(exc.value)
    assert "incident packet not found" in message
    assert ".autodev/autonomous_incident_packet.json" in message


def test_report_and_summary_expose_incident_send(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-summary"
    artifacts = run_dir / ".autodev"

    incident_send_payload = {
        "schema_version": "av3-008-v1",
        "trigger": "autonomous.run_failed",
        "run_dir": str(run_dir),
        "dry_run": True,
        "ok": True,
        "targets": ["stdout:markdown"],
        "success_count": 1,
        "failure_count": 0,
    }

    state = {
        "run_id": "run-summary",
        "request_id": "req-summary",
        "run_out": str(run_dir),
        "profile": "minimal",
        "attempts": [],
    }
    report, report_md = autonomous_mode._render_report(
        state,
        ok=False,
        last_validation=[],
        incident_send=incident_send_payload,
    )

    _write_json(artifacts / "autonomous_report.json", report)
    _write_json(
        artifacts / "autonomous_incident_send.json",
        {
            "schema_version": "av3-008-v1",
            "latest": incident_send_payload,
            "attempts": [incident_send_payload],
        },
    )

    summary = autonomous_mode.extract_autonomous_summary(str(run_dir))
    assert report["incident_send_attempted"] is True
    assert report["incident_send"]["trigger"] == "autonomous.run_failed"
    assert "## Incident Send" in report_md
    assert "Attempted: yes" in report_md

    assert summary["incident_send"]["status"] == "ok"
    assert summary["incident_send"]["payload"]["latest"]["trigger"] == "autonomous.run_failed"
    assert summary["incident_send_status"] == "ok"


def test_webhook_target_sends_signed_payload_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run-webhook-ok"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    monkeypatch.setenv("AUTODEV_WEBHOOK_SECRET", "super-secret")

    captured: dict[str, object] = {}

    def _fake_post_webhook(*, url: str, body: bytes, headers: dict[str, str], timeout_sec: float) -> tuple[int, str]:
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        captured["timeout_sec"] = timeout_sec
        return 202, "accepted"

    monkeypatch.setattr(incident_send, "_post_webhook", _fake_post_webhook)

    result = incident_send.send_incident_packet(
        run_dir=run_dir,
        targets=["webhook:markdown"],
        dry_run=False,
        trigger="autonomous.run_failed",
        target_configs={
            "webhook": {
                "url": "https://example.test/hooks/autodev",
                "signature_secret_env": "AUTODEV_WEBHOOK_SECRET",
                "max_attempts": 1,
            }
        },
    )

    assert result["ok"] is True
    attempt = result["attempts"][0]
    assert attempt["status"] == "sent"
    details = attempt["details"]
    assert details["signed"] is True
    assert details["status_code"] == 202
    headers = captured["headers"]
    assert isinstance(headers, dict)
    signature = headers["X-Autodev-Signature"]
    assert isinstance(signature, str)

    body = captured["body"]
    assert isinstance(body, bytes)
    expected = "sha256=" + hmac.new(b"super-secret", body, hashlib.sha256).hexdigest()
    assert signature == expected


def test_webhook_target_retries_on_transient_failure_with_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run-webhook-retry"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    monkeypatch.setenv("AUTODEV_WEBHOOK_SECRET", "retry-secret")

    attempts = {"count": 0}

    def _fake_post_webhook(*, url: str, body: bytes, headers: dict[str, str], timeout_sec: float) -> tuple[int, str]:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise urllib_error.URLError("temporary network issue")
        return 200, "ok"

    sleeps: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(incident_send, "_post_webhook", _fake_post_webhook)
    monkeypatch.setattr(incident_send.time, "sleep", _fake_sleep)

    result = incident_send.send_incident_packet(
        run_dir=run_dir,
        targets=["webhook"],
        dry_run=False,
        trigger="autonomous.run_failed",
        target_configs={
            "webhook": {
                "url": "https://example.test/hooks/autodev",
                "signature_secret_env": "AUTODEV_WEBHOOK_SECRET",
                "max_attempts": 3,
                "backoff_initial_sec": 0.1,
                "backoff_multiplier": 2,
                "backoff_max_sec": 1,
            }
        },
    )

    assert result["ok"] is True
    details = result["attempts"][0]["details"]
    diag_attempts = details["attempts"]
    assert len(diag_attempts) == 3
    assert diag_attempts[0]["retryable"] is True
    assert diag_attempts[1]["retryable"] is True
    assert diag_attempts[2]["ok"] is True
    assert sleeps == [0.1, 0.2]


def test_webhook_target_does_not_retry_on_permanent_4xx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run-webhook-4xx"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    monkeypatch.setenv("AUTODEV_WEBHOOK_SECRET", "retry-secret")

    attempts = {"count": 0}

    def _fake_post_webhook(*, url: str, body: bytes, headers: dict[str, str], timeout_sec: float) -> tuple[int, str]:
        attempts["count"] += 1
        return 400, "bad request"

    sleeps: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(incident_send, "_post_webhook", _fake_post_webhook)
    monkeypatch.setattr(incident_send.time, "sleep", _fake_sleep)

    result = incident_send.send_incident_packet(
        run_dir=run_dir,
        targets=["webhook"],
        dry_run=False,
        trigger="autonomous.run_failed",
        target_configs={
            "webhook": {
                "url": "https://example.test/hooks/autodev",
                "signature_secret_env": "AUTODEV_WEBHOOK_SECRET",
                "max_attempts": 5,
            }
        },
    )

    assert result["ok"] is False
    attempt = result["attempts"][0]
    assert attempt["status"] == "failed"
    assert "non-retryable status: 400" in attempt["error"]
    assert attempts["count"] == 1
    assert sleeps == []


def test_webhook_target_missing_config_has_clear_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run-webhook-missing-config"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    monkeypatch.delenv("AUTODEV_INCIDENT_WEBHOOK_URL", raising=False)

    result = incident_send.send_incident_packet(
        run_dir=run_dir,
        targets=["webhook"],
        dry_run=False,
        trigger="autonomous.run_failed",
        target_configs={"webhook": {}},
    )

    assert result["ok"] is False
    attempt = result["attempts"][0]
    assert attempt["status"] == "failed"
    details = attempt["details"]
    assert details["code"] == "webhook_url_missing"


def test_webhook_target_missing_secret_has_clear_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = tmp_path / "run-webhook-missing-secret"
    _write_json(run_dir / ".autodev" / "autonomous_incident_packet.json", _sample_packet())

    monkeypatch.delenv("AUTODEV_INCIDENT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("AUTODEV_WEBHOOK_SECRET", raising=False)

    result = incident_send.send_incident_packet(
        run_dir=run_dir,
        targets=["webhook"],
        dry_run=False,
        trigger="autonomous.run_failed",
        target_configs={
            "webhook": {
                "url": "https://example.test/hooks/autodev",
                "signature_secret_env": "AUTODEV_WEBHOOK_SECRET",
                "max_attempts": 1,
            }
        },
    )

    assert result["ok"] is False
    attempt = result["attempts"][0]
    assert attempt["status"] == "failed"
    details = attempt["details"]
    assert details["code"] == "webhook_secret_missing"
    assert details["expected_env"] == "AUTODEV_WEBHOOK_SECRET"
