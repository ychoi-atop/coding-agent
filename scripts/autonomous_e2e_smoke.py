#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autodev.autonomous_evidence_schema import AUTONOMOUS_EVIDENCE_SCHEMA_VERSION


class _FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.model = str(kwargs.get("model") or "fake-model")

    def usage_summary(self) -> dict[str, int]:
        return {
            "total_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }


def _now_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_config(path: Path) -> None:
    path.write_text(
        """\
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: smoke-key
  model: fake-model
profiles:
  minimal:
    validators:
      - ruff
      - pytest
    template_candidates:
      - python_fastapi
run:
  autonomous:
    max_iterations: 5
    time_budget_sec: 600
    quality_gate_policy:
      tests:
        min_pass_rate: 0.9
      security:
        max_high_findings: 0
      performance:
        max_regression_pct: 5
    stop_guard_policy:
      max_consecutive_gate_failures: 2
      max_consecutive_no_improvement: 5
      rollback_recommendation_enabled: true
""",
        encoding="utf-8",
    )


def _http_json(method: str, url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    req = Request(url=url, method=method.upper(), headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"request failed {method} {url}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON response for {method} {url}: {raw[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected non-object JSON response for {method} {url}: {payload}")
    return payload


def _wait_for_health(base_url: str, timeout_sec: float = 20.0) -> None:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            payload = _http_json("GET", f"{base_url}/healthz", timeout=2.0)
            if payload.get("ok") is True:
                return
            last_error = f"unexpected payload: {payload}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"GUI server failed health check within {timeout_sec}s: {last_error}")


def _run_summary_cli(run_dir: Path, fmt: str = "json") -> str:
    commands = [
        ["autodev", "autonomous", "summary", "--run-dir", str(run_dir), "--format", fmt],
        [sys.executable, "-m", "autodev.main", "autonomous", "summary", "--run-dir", str(run_dir), "--format", fmt],
    ]
    failures: list[str] = []
    for cmd in commands:
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            failures.append(f"cmd={cmd!r} missing_executable={exc}")
            continue
        if proc.returncode == 0:
            return proc.stdout
        failures.append(
            f"cmd={cmd!r} rc={proc.returncode} stdout={proc.stdout[-400:]} stderr={proc.stderr[-400:]}"
        )
    raise RuntimeError("summary command failed across all runners: " + " | ".join(failures))


def _latest_run_dir(out_root: Path) -> Path:
    run_dirs = sorted([p for p in out_root.iterdir() if p.is_dir()])
    if not run_dirs:
        raise RuntimeError(f"no run directories under {out_root}")
    return run_dirs[-1]


def run_smoke(*, artifacts_dir: Path, keep_tmp: bool) -> Path:
    # Import here so script startup remains cheap and this can run from repo root directly.
    import autodev.autonomous_mode as autonomous_mode

    run_stamp = _now_stamp()
    run_artifacts = artifacts_dir / run_stamp
    run_artifacts.mkdir(parents=True, exist_ok=True)

    tmp_root = Path(tempfile.mkdtemp(prefix="av2-013-autonomous-smoke-"))
    snapshots: dict[str, Any] = {}
    server_proc: subprocess.Popen[str] | None = None

    server_stdout = run_artifacts / "gui-server.stdout.log"
    server_stderr = run_artifacts / "gui-server.stderr.log"

    original_client = autonomous_mode.LLMClient
    original_runner = autonomous_mode.run_autodev_enterprise

    calls = {"count": 0}

    async def _fake_run(*_args: Any, **_kwargs: Any) -> tuple[bool, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        calls["count"] += 1
        return (
            True,
            {"project": {"type": "autonomous-smoke"}},
            {"tasks": []},
            [
                {
                    "name": "pytest",
                    "ok": False,
                    "status": "failed",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "tests failed in deterministic smoke",
                    "diagnostics": {},
                }
            ],
        )

    try:
        prd = tmp_root / "PRD-smoke.md"
        prd.write_text("# AV2-013 autonomous smoke PRD\n", encoding="utf-8")

        cfg = tmp_root / "config.yaml"
        _write_config(cfg)

        out_root = tmp_root / "generated_runs"
        out_root.mkdir(parents=True, exist_ok=True)

        autonomous_mode.LLMClient = _FakeClient
        autonomous_mode.run_autodev_enterprise = _fake_run

        start_args = [
            "start",
            "--prd",
            str(prd),
            "--out",
            str(out_root),
            "--config",
            str(cfg),
            "--profile",
            "minimal",
            "--max-iterations",
            "5",
            "--workspace-allowlist",
            str(tmp_root),
        ]

        try:
            autonomous_mode.cli(start_args)
            raise RuntimeError("expected autonomous start to fail via stop guard, but it completed")
        except SystemExit as exc:
            if int(exc.code or 0) != 1:
                raise RuntimeError(f"unexpected autonomous start exit code: {exc.code}") from exc

        run_dir = _latest_run_dir(out_root)
        snapshots["run_dir"] = str(run_dir)

        state = json.loads((run_dir / ".autodev" / "autonomous_state.json").read_text(encoding="utf-8"))
        gate_results = json.loads((run_dir / ".autodev" / "autonomous_gate_results.json").read_text(encoding="utf-8"))
        guard = json.loads((run_dir / ".autodev" / "autonomous_guard_decisions.json").read_text(encoding="utf-8"))
        strategy_trace = json.loads((run_dir / ".autodev" / "autonomous_strategy_trace.json").read_text(encoding="utf-8"))
        report = json.loads((run_dir / ".autodev" / "autonomous_report.json").read_text(encoding="utf-8"))

        snapshots["schema_version"] = AUTONOMOUS_EVIDENCE_SCHEMA_VERSION
        snapshots["state"] = {
            "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
            "status": state.get("status"),
            "failure_reason": state.get("failure_reason"),
            "current_iteration": state.get("current_iteration"),
            "preflight": state.get("preflight"),
        }
        snapshots["report"] = report
        snapshots["gate_results"] = gate_results
        snapshots["strategy_trace"] = strategy_trace
        snapshots["guard"] = guard

        preflight = state.get("preflight") if isinstance(state.get("preflight"), dict) else {}
        if preflight.get("status") != "passed":
            raise RuntimeError(f"preflight did not pass: {preflight}")
        if calls["count"] != 2:
            raise RuntimeError(f"expected 2 autonomous attempts before guard stop, got {calls['count']}")

        latest_guard = guard.get("latest") if isinstance(guard.get("latest"), dict) else {}
        if latest_guard.get("reason_code") != "autonomous_guard.repeated_gate_failure_limit_reached":
            raise RuntimeError(f"unexpected guard decision: {latest_guard}")

        summary_json_raw = _run_summary_cli(run_dir, fmt="json")
        summary_json = json.loads(summary_json_raw)
        if isinstance(summary_json, dict):
            summary_json.setdefault("schema_version", AUTONOMOUS_EVIDENCE_SCHEMA_VERSION)
        snapshots["summary_json"] = summary_json

        if summary_json.get("preflight_status") != "passed":
            raise RuntimeError(f"summary preflight status mismatch: {summary_json}")
        if (summary_json.get("gate_counts") or {}).get("fail", 0) < 1:
            raise RuntimeError(f"summary gate fail count mismatch: {summary_json}")
        guard_decision = summary_json.get("guard_decision") if isinstance(summary_json.get("guard_decision"), dict) else {}
        if guard_decision.get("reason_code") != "autonomous_guard.repeated_gate_failure_limit_reached":
            raise RuntimeError(f"summary guard decision mismatch: {summary_json}")

        summary_text = _run_summary_cli(run_dir, fmt="text")
        snapshots["summary_text"] = summary_text
        if "# Autonomous Run Summary" not in summary_text:
            raise RuntimeError("summary text output missing heading")

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"

        with server_stdout.open("w", encoding="utf-8") as out_fp, server_stderr.open("w", encoding="utf-8") as err_fp:
            server_proc = subprocess.Popen(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "autodev.gui_mvp_server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--runs-root",
                    str(out_root),
                ],
                cwd=str(REPO_ROOT),
                stdout=out_fp,
                stderr=err_fp,
                text=True,
                env=os.environ.copy(),
            )

            _wait_for_health(base_url)
            api_snapshot = _http_json("GET", f"{base_url}/api/autonomous/quality-gate/latest")
            if isinstance(api_snapshot, dict):
                api_snapshot.setdefault("schema_version", AUTONOMOUS_EVIDENCE_SCHEMA_VERSION)
                api_summary = api_snapshot.get("summary")
                if isinstance(api_summary, dict):
                    api_summary.setdefault("schema_version", AUTONOMOUS_EVIDENCE_SCHEMA_VERSION)
            snapshots["quality_gate_latest"] = api_snapshot

            latest = api_snapshot.get("latest") if isinstance(api_snapshot.get("latest"), dict) else {}
            latest_path = str(latest.get("path") or "")
            if not latest_path.endswith(run_dir.name):
                raise RuntimeError(f"snapshot latest path mismatch: expected_suffix={run_dir.name} payload={api_snapshot}")

            summary = api_snapshot.get("summary") if isinstance(api_snapshot.get("summary"), dict) else {}
            if summary.get("preflight_status") != "passed":
                raise RuntimeError(f"snapshot preflight mismatch: {api_snapshot}")
            if (summary.get("gate_counts") or {}).get("fail", 0) < 1:
                raise RuntimeError(f"snapshot gate count mismatch: {api_snapshot}")

            snapshot_guard = summary.get("guard_decision") if isinstance(summary.get("guard_decision"), dict) else {}
            if snapshot_guard.get("reason_code") != "autonomous_guard.repeated_gate_failure_limit_reached":
                raise RuntimeError(f"snapshot guard decision mismatch: {api_snapshot}")

        _write_json(
            run_artifacts / "result.json",
            {
                "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
                "ok": True,
                "artifacts": str(run_artifacts),
                "run_dir": str(run_dir),
                "attempts": calls["count"],
                "guard_reason_code": "autonomous_guard.repeated_gate_failure_limit_reached",
            },
        )
        _write_json(run_artifacts / "snapshots.json", snapshots)

    except Exception as exc:  # noqa: BLE001
        snapshots["error"] = {"message": str(exc), "type": type(exc).__name__}
        _write_json(run_artifacts / "snapshots.json", snapshots)
        _write_json(
            run_artifacts / "result.json",
            {
                "schema_version": AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
                "ok": False,
                "error": str(exc),
                "artifacts": str(run_artifacts),
            },
        )
        raise
    finally:
        autonomous_mode.LLMClient = original_client
        autonomous_mode.run_autodev_enterprise = original_runner

        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait(timeout=5)

        if keep_tmp:
            _write_json(run_artifacts / "tmp_root.json", {"tmp_root": str(tmp_root)})
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

    return run_artifacts


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="AV2-013 autonomous E2E smoke lane")
    ap.add_argument(
        "--artifacts-dir",
        default=str(REPO_ROOT / "artifacts" / "autonomous-e2e-smoke"),
        help="directory to persist smoke logs and snapshots",
    )
    ap.add_argument(
        "--keep-tmp",
        action="store_true",
        help="keep temporary generated workspace for debugging",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_artifacts = run_smoke(artifacts_dir=artifacts_dir, keep_tmp=bool(args.keep_tmp))
    except Exception as exc:  # noqa: BLE001
        print(f"[AV2-013 smoke] FAIL: {exc}")
        print(f"[AV2-013 smoke] See artifacts: {artifacts_dir}")
        return 1

    print("[AV2-013 smoke] PASS")
    print(f"[AV2-013 smoke] Artifacts: {run_artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
