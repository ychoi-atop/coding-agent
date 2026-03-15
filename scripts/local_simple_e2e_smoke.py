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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url=url, method=method.upper(), data=body, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed {method} {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response for {method} {url}: {raw[:500]}") from exc


def _http_text(url: str, *, timeout: float = 5.0) -> str:
    req = Request(url=url, method="GET", headers={"Accept": "text/html, text/plain;q=0.9, */*;q=0.1"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for GET {url}: {raw}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed GET {url}: {exc}") from exc


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


def _poll_terminal_process(base_url: str, process_id: str, timeout_sec: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _http_json("GET", f"{base_url}/api/processes/{process_id}")
        last_payload = payload
        state = str(payload.get("state") or "")
        if state in {"exited", "terminated", "killed"}:
            return payload
        time.sleep(0.5)
    raise RuntimeError(f"process {process_id} did not reach terminal state in {timeout_sec}s: {last_payload}")


def _create_fake_autodev(fake_bin_dir: Path) -> Path:
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    fake = fake_bin_dir / "autodev"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time
import uuid
from pathlib import Path


def _arg(flag: str):
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return ''

prd = _arg('--prd')
out = _arg('--out')
profile = _arg('--profile')
model = _arg('--model')

if not out:
    print('missing --out', file=sys.stderr)
    sys.exit(2)

run_id = f"run-smoke-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
run_dir = Path(out) / run_id
ad = run_dir / '.autodev'
ad.mkdir(parents=True, exist_ok=True)

started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
time.sleep(0.25)
ended = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

(ad / 'run_metadata.json').write_text(json.dumps({
    'run_id': run_id,
    'request_id': f'req-{uuid.uuid4().hex[:8]}',
    'requested_profile': profile or 'local_simple',
    'llm': {'model': model or 'fake-model'},
    'run_started_at': started,
    'run_completed_at': ended,
}), encoding='utf-8')

(ad / 'checkpoint.json').write_text(json.dumps({
    'run_id': run_id,
    'completed_task_ids': ['task-001'],
    'failed_task_id': None,
}), encoding='utf-8')

(ad / 'run_trace.json').write_text(json.dumps({
    'run_id': run_id,
    'model': model or 'fake-model',
    'started_at': started,
    'completed_at': ended,
    'events': [
        {'phase': 'kickoff', 'at': started},
        {'phase': 'complete', 'at': ended},
    ],
}), encoding='utf-8')

(ad / 'task_quality_index.json').write_text(json.dumps({
    'project': {'type': 'smoke'},
    'tasks': [
        {'task_id': 'task-001', 'status': 'passed'},
    ],
    'totals': {
        'total_task_attempts': 1,
        'hard_failures': 0,
        'soft_failures': 0,
        'repair_passes': 0,
    },
    'final': {'status': 'ok'},
    'resolved_quality_profile': {'name': profile or 'local_simple'},
    'unresolved_blockers': [],
}), encoding='utf-8')

(ad / 'task_final_last_validation.json').write_text(json.dumps({
    'summary': {'total': 1, 'passed': 1, 'failed': 0, 'soft_fail': 0, 'skipped': 0, 'blocking_failed': 0},
    'validators': [
        {'name': 'smoke', 'status': 'passed', 'ok': True, 'artifact_path': '.autodev/task_final_last_validation.json'}
    ],
    'meta': {'prd': prd},
}), encoding='utf-8')

sys.exit(0)
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _write_trust_run_fixture(
    run_dir: Path,
    *,
    run_id: str,
    profile: str,
    model: str,
    status: str,
    total_task_attempts: int,
    hard_failures: int,
    soft_failures: int,
    blocker_count: int,
    validation_passed: int,
    validation_failed: int,
    quality_score: float,
    trust_owner: str,
    trust_severity: str,
) -> None:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2))
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    artifacts = run_dir / ".autodev"
    artifacts.mkdir(parents=True, exist_ok=True)

    task_rows: list[dict[str, Any]] = []
    for index in range(max(validation_passed, 1 if validation_failed == 0 else 0)):
        task_rows.append({"task_id": f"task-pass-{index + 1:03d}", "status": "passed"})
    for index in range(validation_failed):
        task_rows.append({"task_id": f"task-fail-{index + 1:03d}", "status": "failed"})

    validator_rows: list[dict[str, Any]] = []
    for index in range(validation_passed):
        validator_rows.append(
            {
                "name": f"validator-pass-{index + 1:03d}",
                "status": "passed",
                "ok": True,
                "artifact_path": ".autodev/task_final_last_validation.json",
            }
        )
    for index in range(validation_failed):
        validator_rows.append(
            {
                "name": f"validator-fail-{index + 1:03d}",
                "status": "failed",
                "ok": False,
                "artifact_path": ".autodev/task_final_last_validation.json",
            }
        )

    blocker_rows = [f"blocker-{index + 1:03d}" for index in range(blocker_count)]
    gate_passed = hard_failures == 0 and validation_failed == 0
    fail_reasons = [] if gate_passed else [{"code": "tests.min_pass_rate_not_met"}]

    _write_json(
        artifacts / "run_metadata.json",
        {
            "run_id": run_id,
            "request_id": f"req-{run_id}",
            "requested_profile": profile,
            "llm": {"model": model},
            "run_started_at": started,
            "run_completed_at": ended,
            "result_ok": status == "completed" and gate_passed,
        },
    )
    _write_json(
        artifacts / "checkpoint.json",
        {
            "run_id": run_id,
            "status": status,
            "completed_task_ids": [row["task_id"] for row in task_rows if row["status"] == "passed"],
            "failed_task_id": task_rows[-1]["task_id"] if validation_failed else None,
        },
    )
    _write_json(
        artifacts / "run_trace.json",
        {
            "run_id": run_id,
            "model": model,
            "started_at": started,
            "completed_at": ended,
            "events": [
                {"phase": "kickoff", "at": started},
                {"phase": "validate", "at": ended},
                {"phase": "complete", "at": ended},
            ],
        },
    )
    _write_json(
        artifacts / "task_quality_index.json",
        {
            "project": {"type": "smoke"},
            "tasks": task_rows,
            "totals": {
                "total_task_attempts": total_task_attempts,
                "hard_failures": hard_failures,
                "soft_failures": soft_failures,
                "repair_passes": 0,
            },
            "final": {"status": "ok" if gate_passed else "failed"},
            "resolved_quality_profile": {"name": profile},
            "unresolved_blockers": blocker_rows,
        },
    )
    _write_json(
        artifacts / "task_final_last_validation.json",
        {
            "summary": {
                "total": validation_passed + validation_failed,
                "passed": validation_passed,
                "failed": validation_failed,
                "soft_fail": 0,
                "skipped": 0,
                "blocking_failed": validation_failed,
            },
            "validators": validator_rows,
            "validation": validator_rows,
        },
    )
    _write_json(
        artifacts / "autonomous_report.json",
        {
            "ok": gate_passed,
            "run_id": run_id,
            "latest_strategy": {"name": "mixed"},
            "preflight": {"status": "passed", "reason_codes": []},
            "operator_guidance": {
                "top": [
                    {
                        "code": "tests.min_pass_rate_not_met" if not gate_passed else "operator.review.final_artifacts",
                        "actions": [
                            "Inspect validation artifacts and compare trust posture.",
                            "Confirm saved comparison metadata before operator handoff.",
                        ],
                    }
                ]
            },
            "incident_routing": {
                "primary": {
                    "owner_team": trust_owner,
                    "severity": trust_severity,
                    "target_sla": "4h" if trust_severity == "high" else "12h",
                    "escalation_class": "engineering_hotfix" if trust_severity == "high" else "manual_triage",
                }
            },
            "gate_results": {
                "passed": gate_passed,
                "gates": {
                    "composite_quality": {
                        "status": "passed" if gate_passed else "failed",
                        "composite_score": quality_score,
                        "hard_blocked": not gate_passed,
                        "components": {"tests": quality_score},
                    }
                },
                "fail_reasons": fail_reasons,
            },
        },
    )
    _write_json(
        artifacts / "autonomous_gate_results.json",
        {
            "attempts": [
                {
                    "iteration": 1,
                    "gate_results": {
                        "passed": gate_passed,
                        "gates": {
                            "composite_quality": {
                                "status": "passed" if gate_passed else "failed",
                                "composite_score": quality_score,
                                "hard_blocked": not gate_passed,
                                "components": {"tests": quality_score},
                            }
                        },
                        "fail_reasons": fail_reasons,
                    },
                }
            ]
        },
    )
    if not gate_passed:
        _write_json(
            artifacts / "autonomous_incident_packet.json",
            {
                "schema_version": "av3-005-v1",
                "status": "failed",
                "run_summary": {"run_id": run_id},
            },
        )


def _build_compare_snapshot_payload(
    *,
    left_detail: dict[str, Any],
    right_detail: dict[str, Any],
    compare_payload: dict[str, Any],
) -> dict[str, Any]:
    left = compare_payload.get("left") if isinstance(compare_payload.get("left"), dict) else {}
    right = compare_payload.get("right") if isinstance(compare_payload.get("right"), dict) else {}
    left_trust = left.get("trust") if isinstance(left.get("trust"), dict) else {}
    right_trust = right.get("trust") if isinstance(right.get("trust"), dict) else {}
    left_packet = left_detail.get("trust_packet") if isinstance(left_detail.get("trust_packet"), dict) else {}
    right_packet = right_detail.get("trust_packet") if isinstance(right_detail.get("trust_packet"), dict) else {}
    left_score = float(left_trust.get("score") or 0.0)
    right_score = float(right_trust.get("score") or 0.0)
    generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "snapshot": {
            "schema_version": "compare-trust-snapshot-v1",
            "generated_at": generated_at,
            "source": "local-simple-e2e-smoke",
            "left": {
                "run_id": left.get("run_id"),
                "status": left.get("status"),
                "profile": left.get("profile"),
                "model": left.get("model"),
                "trust": left_trust,
                "trust_packet_summary": left_packet,
            },
            "right": {
                "run_id": right.get("run_id"),
                "status": right.get("status"),
                "profile": right.get("profile"),
                "model": right.get("model"),
                "trust": right_trust,
                "trust_packet_summary": right_packet,
            },
            "delta": compare_payload.get("delta", {}),
            "trust_packet_diff": [
                {
                    "path": "trust_signals.overall.status",
                    "left": left_trust.get("status"),
                    "right": right_trust.get("status"),
                },
                {
                    "path": "trust_signals.overall.score",
                    "left": f"{left_score:.2f}",
                    "right": f"{right_score:.2f}",
                },
            ],
            "highlights": [
                f"Trust: {left_trust.get('status') or 'unknown'} ({left_score:.2f}) -> {right_trust.get('status') or 'unknown'} ({right_score:.2f})"
            ],
        },
        "compare_payload": {
            "left": {**left, "trust_packet": left_packet, "trust_message": str(left_detail.get("trust_message") or "")},
            "right": {**right, "trust_packet": right_packet, "trust_message": str(right_detail.get("trust_message") or "")},
            "delta": compare_payload.get("delta", {}),
        },
        "markdown": (
            "# Compare Trust Snapshot\n\n"
            f"- baseline_run: {left.get('run_id') or '-'}\n"
            f"- candidate_run: {right.get('run_id') or '-'}\n"
            f"- trust_delta: {compare_payload.get('delta', {}).get('trust_score', 0.0):+.2f}\n"
        ),
    }


def run_smoke(*, artifacts_dir: Path, keep_tmp: bool) -> Path:
    run_stamp = _now_stamp()
    run_artifacts = artifacts_dir / run_stamp
    run_artifacts.mkdir(parents=True, exist_ok=True)

    tmp_root = Path(tempfile.mkdtemp(prefix="nxt-007-local-simple-"))
    server_proc: subprocess.Popen[str] | None = None
    server_stdout = run_artifacts / "gui-server.stdout.log"
    server_stderr = run_artifacts / "gui-server.stderr.log"

    snapshots: dict[str, Any] = {}

    try:
        fake_bin_dir = tmp_root / "fake-bin"
        _create_fake_autodev(fake_bin_dir)

        runs_root = tmp_root / "generated_runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        prd = tmp_root / "PRD-smoke.md"
        prd.write_text("# Local-simple smoke PRD\n", encoding="utf-8")

        process_state_file = run_artifacts / "gui-process-state.json"

        env = os.environ.copy()
        env["PATH"] = f"{fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["AUTODEV_GUI_LOCAL_SIMPLE"] = "1"
        env["AUTODEV_GUI_ROLE"] = "developer"
        env["AUTODEV_GUI_PROCESS_STATE_FILE"] = str(process_state_file)

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
                    str(runs_root),
                ],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=out_fp,
                stderr=err_fp,
                text=True,
            )

            _wait_for_health(base_url)

            index_html = _http_text(f"{base_url}/index.html")
            static_checks = {
                "has_trust_trend_cards": "trustTrendCards" in index_html,
                "has_saved_comparisons_panel": "Saved Comparisons" in index_html,
                "has_trust_diff_filter": "compareTrustDiffSeveritySelect" in index_html,
            }
            snapshots["static_index"] = static_checks
            if not all(static_checks.values()):
                raise RuntimeError(f"static trust/compare UI markers missing: {static_checks}")

            context = _http_json("GET", f"{base_url}/api/gui/context")
            snapshots["gui_context"] = context
            if context.get("mode") != "local_simple":
                raise RuntimeError(f"expected local_simple mode, got: {context}")

            start_payload = {
                "prd": str(prd),
                "out": str(runs_root),
                "profile": "local_simple",
                "interactive": False,
                "execute": True,
                "correlation_id": f"nxt-007-{run_stamp}",
            }
            kickoff = _http_json("POST", f"{base_url}/api/runs/start", payload=start_payload)
            snapshots["kickoff"] = kickoff
            process_id = str((kickoff.get("process") or {}).get("process_id") or "").strip()
            if not process_id:
                raise RuntimeError(f"missing process_id from kickoff response: {kickoff}")

            process_detail = _poll_terminal_process(base_url, process_id, timeout_sec=30.0)
            snapshots["process_detail_terminal"] = process_detail

            history = _http_json("GET", f"{base_url}/api/processes/{process_id}/history")
            snapshots["process_history"] = history
            history_states = [str(row.get("state") or "") for row in history.get("history", []) if isinstance(row, dict)]
            if "running" not in history_states:
                raise RuntimeError(f"process history missing running state: {history}")
            if not any(state in {"exited", "terminated", "killed"} for state in history_states):
                raise RuntimeError(f"process history missing terminal state: {history}")

            processes = _http_json("GET", f"{base_url}/api/processes?{urlencode({'limit': 20})}")
            snapshots["processes"] = processes

            runs_payload = _http_json("GET", f"{base_url}/api/runs")
            snapshots["runs"] = runs_payload
            runs = runs_payload.get("runs") if isinstance(runs_payload, dict) else []
            if not isinstance(runs, list) or not runs:
                raise RuntimeError(f"expected at least one run after kickoff, got: {runs_payload}")
            run_id = str((runs[0] or {}).get("run_id") or "").strip()
            if not run_id:
                raise RuntimeError(f"latest run row missing run_id: {runs[0]}")

            artifact_query = urlencode({"path": ".autodev/task_final_last_validation.json", "max_bytes": 512000})
            artifact_payload = _http_json(
                "GET",
                f"{base_url}/api/runs/{run_id}/artifacts/read?{artifact_query}",
            )
            snapshots["artifact_read"] = artifact_payload

            if str(artifact_payload.get("content_type")) != "application/json":
                raise RuntimeError(f"artifact content_type mismatch: {artifact_payload}")
            content = artifact_payload.get("content")
            if not isinstance(content, dict):
                raise RuntimeError(f"artifact content is not parsed JSON object: {artifact_payload}")
            summary = content.get("summary") if isinstance(content, dict) else None
            if not isinstance(summary, dict) or int(summary.get("passed", 0)) < 1:
                raise RuntimeError(f"artifact summary does not indicate pass: {artifact_payload}")

            baseline_run_id = "run-smoke-baseline"
            baseline_run_dir = runs_root / baseline_run_id
            candidate_run_dir = runs_root / run_id

            _write_trust_run_fixture(
                baseline_run_dir,
                run_id=baseline_run_id,
                profile="local_simple",
                model="fake-model-baseline",
                status="failed",
                total_task_attempts=2,
                hard_failures=1,
                soft_failures=0,
                blocker_count=1,
                validation_passed=0,
                validation_failed=1,
                quality_score=41.0,
                trust_owner="Feature Engineering",
                trust_severity="high",
            )
            _write_trust_run_fixture(
                candidate_run_dir,
                run_id=run_id,
                profile="local_simple",
                model="fake-model",
                status="completed",
                total_task_attempts=1,
                hard_failures=0,
                soft_failures=0,
                blocker_count=0,
                validation_passed=1,
                validation_failed=0,
                quality_score=96.0,
                trust_owner="Autonomy On-Call",
                trust_severity="medium",
            )

            trust_latest = _http_json("GET", f"{base_url}/api/autonomous/trust/latest")
            snapshots["trust_latest"] = trust_latest
            if trust_latest.get("empty") is not False:
                raise RuntimeError(f"expected populated trust latest payload: {trust_latest}")

            trust_trends = _http_json("GET", f"{base_url}/api/autonomous/trust/trends?window=5")
            snapshots["trust_trends"] = trust_trends
            trend_summary = trust_trends.get("summary") if isinstance(trust_trends, dict) else {}
            if not isinstance(trend_summary, dict) or int(trend_summary.get("runs_considered", 0)) < 2:
                raise RuntimeError(f"expected trust trends to consider at least 2 runs: {trust_trends}")

            baseline_detail = _http_json("GET", f"{base_url}/api/runs/{baseline_run_id}")
            candidate_detail = _http_json("GET", f"{base_url}/api/runs/{run_id}")
            snapshots["baseline_run_detail"] = baseline_detail
            snapshots["candidate_run_detail"] = candidate_detail
            if not isinstance(baseline_detail.get("trust_summary"), dict) or not isinstance(candidate_detail.get("trust_summary"), dict):
                raise RuntimeError("expected trust summaries on both run detail payloads")

            compare_payload = _http_json("GET", f"{base_url}/api/runs/compare?left={baseline_run_id}&right={run_id}")
            snapshots["compare"] = compare_payload
            compare_delta = compare_payload.get("delta") if isinstance(compare_payload, dict) else {}
            if not isinstance(compare_delta, dict) or compare_delta.get("trust_status_changed") is not True:
                raise RuntimeError(f"expected trust compare delta to show status change: {compare_payload}")

            compare_snapshot_payload = _build_compare_snapshot_payload(
                left_detail=baseline_detail,
                right_detail=candidate_detail,
                compare_payload=compare_payload,
            )
            compare_save = _http_json("POST", f"{base_url}/api/runs/compare/snapshots", payload=compare_snapshot_payload)
            snapshots["compare_snapshot_save"] = compare_save
            saved_snapshot = compare_save.get("snapshot") if isinstance(compare_save, dict) else {}
            snapshot_id = str((saved_snapshot or {}).get("snapshot_id") or "").strip()
            if not snapshot_id:
                raise RuntimeError(f"missing compare snapshot id from save response: {compare_save}")

            compare_list = _http_json("GET", f"{base_url}/api/runs/compare/snapshots?query=smoke")
            snapshots["compare_snapshot_list"] = compare_list
            list_rows = compare_list.get("snapshots") if isinstance(compare_list, dict) else []
            if not isinstance(list_rows, list) or not any(str(row.get("snapshot_id") or "") == snapshot_id for row in list_rows if isinstance(row, dict)):
                raise RuntimeError(f"saved compare snapshot not visible in list response: {compare_list}")

            compare_detail = _http_json("GET", f"{base_url}/api/runs/compare/snapshots/{snapshot_id}")
            snapshots["compare_snapshot_detail"] = compare_detail
            if str((compare_detail.get("snapshot") or {}).get("snapshot_id") or "") != snapshot_id:
                raise RuntimeError(f"saved compare snapshot detail mismatch: {compare_detail}")

            compare_update = _http_json(
                "PATCH",
                f"{base_url}/api/runs/compare/snapshots/{snapshot_id}",
                payload={"display_name": "Smoke compare snapshot", "pinned": True, "tags": ["smoke", "trust"]},
            )
            snapshots["compare_snapshot_update"] = compare_update
            updated_snapshot = compare_update.get("snapshot") if isinstance(compare_update, dict) else {}
            if not isinstance(updated_snapshot, dict) or updated_snapshot.get("pinned") is not True:
                raise RuntimeError(f"compare snapshot metadata update failed: {compare_update}")

            compare_delete = _http_json("DELETE", f"{base_url}/api/runs/compare/snapshots/{snapshot_id}")
            snapshots["compare_snapshot_delete"] = compare_delete
            if compare_delete.get("deleted") is not True:
                raise RuntimeError(f"compare snapshot delete failed: {compare_delete}")

            compare_list_after_delete = _http_json("GET", f"{base_url}/api/runs/compare/snapshots")
            snapshots["compare_snapshot_list_after_delete"] = compare_list_after_delete
            remaining_rows = compare_list_after_delete.get("snapshots") if isinstance(compare_list_after_delete, dict) else []
            if isinstance(remaining_rows, list) and any(str(row.get("snapshot_id") or "") == snapshot_id for row in remaining_rows if isinstance(row, dict)):
                raise RuntimeError(f"deleted compare snapshot still present in list response: {compare_list_after_delete}")

            _write_json(
                run_artifacts / "result.json",
                {
                    "ok": True,
                    "base_url": base_url,
                    "process_id": process_id,
                    "run_id": run_id,
                    "baseline_run_id": baseline_run_id,
                    "compare_snapshot_id": snapshot_id,
                    "artifacts": str(run_artifacts),
                },
            )
            _write_json(run_artifacts / "snapshots.json", snapshots)

    except Exception as exc:  # noqa: BLE001
        snapshots["error"] = {"message": str(exc), "type": type(exc).__name__}
        _write_json(run_artifacts / "snapshots.json", snapshots)
        _write_json(
            run_artifacts / "result.json",
            {
                "ok": False,
                "error": str(exc),
                "artifacts": str(run_artifacts),
            },
        )
        raise
    finally:
        if server_proc is not None and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait(timeout=5)

        if not keep_tmp:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            _write_json(run_artifacts / "tmp_root.json", {"tmp_root": str(tmp_root)})

    return run_artifacts


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="NXT-007 local-simple GUI E2E smoke")
    ap.add_argument(
        "--artifacts-dir",
        default=str(REPO_ROOT / "artifacts" / "local-simple-e2e-smoke"),
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
        print(f"[NXT-007 smoke] FAIL: {exc}")
        print(f"[NXT-007 smoke] See artifacts: {artifacts_dir}")
        return 1

    print("[NXT-007 smoke] PASS")
    print(f"[NXT-007 smoke] Artifacts: {run_artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
