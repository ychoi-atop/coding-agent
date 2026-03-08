from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .autonomous_incident_export import SUPPORTED_EXPORT_FORMATS, load_incident_packet, render_incident_export

logger = logging.getLogger("autodev")

AUTONOMOUS_INCIDENT_SEND_VERSION = "av3-007-v1"
AUTONOMOUS_INCIDENT_SEND_JSON = ".autodev/autonomous_incident_send.json"
_DEFAULT_TARGET_FORMAT = "markdown"


@dataclass(frozen=True)
class IncidentSendTargetSpec:
    target: str
    export_format: str = _DEFAULT_TARGET_FORMAT


IncidentSendHandler = Callable[[dict[str, Any], str, bool, dict[str, Any]], dict[str, Any] | None]


_INCIDENT_SEND_TARGETS: dict[str, IncidentSendHandler] = {}


def register_incident_send_target(name: str, handler: IncidentSendHandler) -> None:
    target_name = str(name or "").strip().lower()
    if not target_name:
        raise ValueError("incident send target name must be non-empty")
    _INCIDENT_SEND_TARGETS[target_name] = handler


def available_incident_send_targets() -> list[str]:
    return sorted(_INCIDENT_SEND_TARGETS.keys())


def parse_incident_send_target_specs(targets: list[str] | None) -> list[IncidentSendTargetSpec]:
    requested = targets or ["stdout"]
    specs: list[IncidentSendTargetSpec] = []
    for raw in requested:
        token = str(raw or "").strip()
        if not token:
            raise ValueError("incident send target cannot be empty")

        target_name = token
        export_format = _DEFAULT_TARGET_FORMAT
        if ":" in token:
            left, right = token.split(":", 1)
            target_name = left.strip()
            export_format = right.strip() or _DEFAULT_TARGET_FORMAT

        target_name = target_name.lower()
        if not target_name:
            raise ValueError("incident send target cannot be empty")
        if target_name not in _INCIDENT_SEND_TARGETS:
            raise ValueError(
                f"unsupported incident send target: {target_name} "
                f"(available: {', '.join(available_incident_send_targets())})"
            )
        if export_format not in SUPPORTED_EXPORT_FORMATS:
            raise ValueError(
                f"unsupported incident export format: {export_format} "
                f"(expected one of: {', '.join(SUPPORTED_EXPORT_FORMATS)})"
            )

        specs.append(IncidentSendTargetSpec(target=target_name, export_format=export_format))

    return specs


def send_incident_packet(
    *,
    run_dir: str | Path,
    targets: list[str] | None,
    dry_run: bool,
    trigger: str,
) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    packet = load_incident_packet(run_path)
    specs = parse_incident_send_target_specs(targets)

    attempts: list[dict[str, Any]] = []
    for spec in specs:
        handler = _INCIDENT_SEND_TARGETS[spec.target]
        rendered = render_incident_export(packet, spec.export_format)
        attempt: dict[str, Any] = {
            "target": spec.target,
            "format": spec.export_format,
            "dry_run": dry_run,
        }
        try:
            details = handler(packet, rendered, dry_run, {"run_dir": str(run_path), "trigger": trigger, "target": spec.target})
            attempt["ok"] = True
            attempt["status"] = "dry_run" if dry_run else "sent"
            if isinstance(details, dict) and details:
                attempt["details"] = details
        except Exception as e:  # pragma: no cover - defensive guard
            attempt["ok"] = False
            attempt["status"] = "failed"
            attempt["error"] = str(e)
        attempts.append(attempt)

    ok = all(bool(item.get("ok")) for item in attempts)
    return {
        "schema_version": AUTONOMOUS_INCIDENT_SEND_VERSION,
        "trigger": trigger,
        "run_dir": str(run_path),
        "packet_status": str(packet.get("status") or "unknown"),
        "packet_run_id": str((packet.get("run_summary") or {}).get("run_id") or "-"),
        "dry_run": dry_run,
        "targets": [f"{item.target}:{item.export_format}" for item in specs],
        "ok": ok,
        "attempt_count": len(attempts),
        "success_count": len([item for item in attempts if item.get("ok") is True]),
        "failure_count": len([item for item in attempts if item.get("ok") is not True]),
        "attempts": attempts,
    }


def _stdout_target(_packet: dict[str, Any], rendered: str, dry_run: bool, _context: dict[str, Any]) -> dict[str, Any]:
    if dry_run:
        preview = "\n".join(rendered.splitlines()[:3])
        return {"printed": False, "preview": preview}
    print(rendered)
    return {"printed": True, "chars": len(rendered)}


def _log_target(packet: dict[str, Any], rendered: str, dry_run: bool, context: dict[str, Any]) -> dict[str, Any]:
    run_summary = packet.get("run_summary") if isinstance(packet.get("run_summary"), dict) else {}
    payload = {
        "event": "autonomous.incident_send",
        "target": "log",
        "dry_run": dry_run,
        "trigger": context.get("trigger"),
        "run_id": run_summary.get("run_id"),
        "request_id": run_summary.get("request_id"),
        "message": rendered,
    }
    logger.info(payload)
    return {"logged": True}


register_incident_send_target("stdout", _stdout_target)
register_incident_send_target("log", _log_target)
