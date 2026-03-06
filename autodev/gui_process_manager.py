from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILE_ENV = "AUTODEV_GUI_PROCESS_STATE_FILE"
DEFAULT_STATE_FILE = "artifacts/gui-process/process-state.json"
TERMINAL_STATES = {"exited", "terminated", "killed"}


@dataclass
class ManagedRunProcess:
    process_id: str
    action: str
    payload: dict[str, Any]
    command: list[str]
    pid: int
    started_at: str
    state: str
    transitions: list[dict[str, Any]] = field(default_factory=list)
    retry_of: str | None = None
    retry_root: str | None = None
    retry_attempt: int = 1
    run_link: dict[str, Any] = field(default_factory=dict)
    returncode: int | None = None
    stop_reason: str | None = None
    _proc: subprocess.Popen[Any] | Any = field(default=None, repr=False)


class GuiRunProcessManager:
    def __init__(self, *, state_file: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ManagedRunProcess] = {}
        self._state_file = _resolve_state_file(state_file)
        self._load_state()

    def spawn(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        command: list[str],
        retry_of: str | None = None,
        run_link: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proc = subprocess.Popen(  # noqa: S603 - sanitized argv + shell=False
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        with self._lock:
            now = _utc_now()
            process_id = f"proc-{uuid.uuid4().hex[:12]}"
            retry_root = process_id
            retry_attempt = 1
            if retry_of:
                parent = self._items.get(retry_of)
                if parent:
                    retry_root = parent.retry_root or parent.process_id
                    retry_attempt = int(parent.retry_attempt) + 1

            item = ManagedRunProcess(
                process_id=process_id,
                action=action,
                payload=dict(payload),
                command=list(command),
                pid=int(proc.pid),
                started_at=now,
                state="running",
                retry_of=retry_of,
                retry_root=retry_root,
                retry_attempt=retry_attempt,
                run_link=_normalize_run_link(run_link),
                _proc=proc,
            )
            self._append_transition(item, "spawned", detail={"pid": item.pid})
            self._append_transition(item, "running")
            self._refresh_state(item)
            self._items[process_id] = item
            self._persist_state()
            return self._snapshot(item)

    def stop(self, process_id: str, *, graceful_timeout_sec: float = 2.0) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(process_id)
            if item is None:
                raise KeyError(process_id)

            self._refresh_state(item)
            if item.state in TERMINAL_STATES:
                self._persist_state()
                return self._snapshot(item)

            proc = item._proc
            if proc is None:
                self._append_transition(item, "stop_unavailable", detail={"reason": "process_handle_unavailable"})
                self._persist_state()
                return self._snapshot(item)

            self._append_transition(item, "stopping", detail={"graceful_timeout_sec": graceful_timeout_sec})
            proc.terminate()
            try:
                rc = proc.wait(timeout=graceful_timeout_sec)
                item.returncode = int(rc) if rc is not None else None
                item.state = "terminated"
                item.stop_reason = "graceful"
                self._append_transition(item, "terminated", detail={"returncode": item.returncode})
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait(timeout=1)
                item.returncode = int(rc) if rc is not None else None
                item.state = "killed"
                item.stop_reason = "forced"
                self._append_transition(item, "killed", detail={"returncode": item.returncode})

            self._persist_state()
            return self._snapshot(item)

    def retry(
        self,
        *,
        process_id: str | None = None,
        run_id: str | None = None,
        execute: bool,
    ) -> dict[str, Any]:
        target = self._resolve_retry_target(process_id=process_id, run_id=run_id)
        command = list(target.command)
        action = target.action
        payload = dict(target.payload)
        run_link = dict(target.run_link)

        if not execute:
            return {
                "ok": True,
                "spawned": False,
                "command": command,
                "retry_of": target.process_id,
                "action": action,
                "run_link": run_link,
            }

        current = self.spawn(
            action=action,
            payload=payload,
            command=command,
            retry_of=target.process_id,
            run_link=run_link,
        )
        return {
            "ok": True,
            "spawned": True,
            "command": command,
            "retry_of": target.process_id,
            "action": action,
            "run_link": run_link,
            "process": current,
        }

    def get(self, process_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(process_id)
            if item is None:
                return None
            self._refresh_state(item)
            self._persist_state()
            return self._snapshot(item)

    def list(self, *, limit: int = 100, state: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[ManagedRunProcess] = []
            for item in self._items.values():
                self._refresh_state(item)
                if state and item.state != state:
                    continue
                if run_id and _run_id_of(item) != run_id:
                    continue
                rows.append(item)
            self._persist_state()

        rows.sort(key=lambda row: row.started_at, reverse=True)
        applied_limit = max(1, int(limit))
        return [self._snapshot(row) for row in rows[:applied_limit]]

    def history(self, process_id: str) -> list[dict[str, Any]]:
        with self._lock:
            item = self._items.get(process_id)
            if item is None:
                raise KeyError(process_id)
            self._refresh_state(item)
            self._persist_state()
            return [dict(entry) for entry in item.transitions]

    def _resolve_retry_target(self, *, process_id: str | None, run_id: str | None) -> ManagedRunProcess:
        with self._lock:
            if process_id:
                item = self._items.get(process_id)
                if item is None:
                    raise KeyError(process_id)
                self._refresh_state(item)
                self._persist_state()
                return item

            if run_id:
                candidates = [item for item in self._items.values() if _run_id_of(item) == run_id]
                if not candidates:
                    raise KeyError(f"run_id:{run_id}")
                candidates.sort(
                    key=lambda it: (it.retry_attempt, it.started_at),
                    reverse=True,
                )
                item = candidates[0]
                self._refresh_state(item)
                self._persist_state()
                return item

        raise KeyError("retry_target_missing")

    def _refresh_state(self, item: ManagedRunProcess) -> None:
        if item.state in TERMINAL_STATES:
            return
        if item._proc is None:
            return
        rc = item._proc.poll()
        if rc is not None:
            item.returncode = int(rc)
            item.state = "exited"
            self._append_transition(item, "exited", detail={"returncode": item.returncode})

    def _append_transition(self, item: ManagedRunProcess, state: str, *, detail: dict[str, Any] | None = None) -> None:
        item.state = state
        event = {"at": _utc_now(), "state": state}
        if detail:
            event["detail"] = detail
        item.transitions.append(event)

    def _snapshot(self, item: ManagedRunProcess) -> dict[str, Any]:
        return {
            "process_id": item.process_id,
            "action": item.action,
            "pid": item.pid,
            "state": item.state,
            "retry_of": item.retry_of,
            "retry_root": item.retry_root,
            "retry_attempt": item.retry_attempt,
            "run_link": dict(item.run_link),
            "started_at": item.started_at,
            "returncode": item.returncode,
            "stop_reason": item.stop_reason,
            "transitions": [dict(entry) for entry in item.transitions],
            "command": list(item.command),
            "payload": dict(item.payload),
        }

    def _persist_state(self) -> None:
        data = {
            "version": 1,
            "updated_at": _utc_now(),
            "processes": [self._serialize_item(item) for item in self._items.values()],
        }
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._state_file)

    def _load_state(self) -> None:
        with self._lock:
            self._items = {}
            if not self._state_file.exists():
                return
            try:
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            if not isinstance(raw, dict):
                return
            processes = raw.get("processes")
            if not isinstance(processes, list):
                return
            for row in processes:
                if not isinstance(row, dict):
                    continue
                item = self._deserialize_item(row)
                if item is None:
                    continue
                self._items[item.process_id] = item

    def _serialize_item(self, item: ManagedRunProcess) -> dict[str, Any]:
        return {
            "process_id": item.process_id,
            "action": item.action,
            "payload": dict(item.payload),
            "command": list(item.command),
            "pid": item.pid,
            "started_at": item.started_at,
            "state": item.state,
            "transitions": [dict(entry) for entry in item.transitions],
            "retry_of": item.retry_of,
            "retry_root": item.retry_root,
            "retry_attempt": item.retry_attempt,
            "run_link": dict(item.run_link),
            "returncode": item.returncode,
            "stop_reason": item.stop_reason,
        }

    def _deserialize_item(self, row: dict[str, Any]) -> ManagedRunProcess | None:
        process_id = str(row.get("process_id") or "").strip()
        if not process_id:
            return None
        command_raw = row.get("command")
        payload_raw = row.get("payload")
        transitions_raw = row.get("transitions")
        run_link_raw = row.get("run_link")

        command = [str(part) for part in command_raw] if isinstance(command_raw, list) else []
        payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
        transitions = [dict(entry) for entry in transitions_raw] if isinstance(transitions_raw, list) else []

        return ManagedRunProcess(
            process_id=process_id,
            action=str(row.get("action") or ""),
            payload=payload,
            command=command,
            pid=int(row.get("pid") or 0),
            started_at=str(row.get("started_at") or ""),
            state=str(row.get("state") or "unknown"),
            transitions=transitions,
            retry_of=str(row.get("retry_of")) if row.get("retry_of") is not None else None,
            retry_root=str(row.get("retry_root")) if row.get("retry_root") is not None else None,
            retry_attempt=int(row.get("retry_attempt") or 1),
            run_link=_normalize_run_link(run_link_raw if isinstance(run_link_raw, dict) else {}),
            returncode=int(row.get("returncode")) if row.get("returncode") is not None else None,
            stop_reason=str(row.get("stop_reason")) if row.get("stop_reason") is not None else None,
            _proc=None,
        )


def _resolve_state_file(state_file: str | Path | None) -> Path:
    if state_file is not None:
        return Path(state_file).expanduser().resolve()
    from_env = os.environ.get(STATE_FILE_ENV, "").strip()
    if from_env:
        return Path(from_env).expanduser().resolve()
    return Path(DEFAULT_STATE_FILE).expanduser().resolve()


def _normalize_run_link(run_link: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(run_link or {})
    run_id = str(normalized.get("run_id") or "").strip()
    out = str(normalized.get("out") or "").strip()
    if not run_id and out:
        run_id = Path(out.rstrip("/")).name
    if run_id:
        normalized["run_id"] = run_id
    if out:
        normalized["out"] = out
    return normalized


def _run_id_of(item: ManagedRunProcess) -> str:
    return str(item.run_link.get("run_id") or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
