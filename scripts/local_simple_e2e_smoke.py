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

            _write_json(
                run_artifacts / "result.json",
                {
                    "ok": True,
                    "base_url": base_url,
                    "process_id": process_id,
                    "run_id": run_id,
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
