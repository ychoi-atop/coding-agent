from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .autonomous_incident_export import SUPPORTED_EXPORT_FORMATS, load_incident_packet, render_incident_export

logger = logging.getLogger("autodev")

AUTONOMOUS_INCIDENT_SEND_VERSION = "av3-011-v1"
AUTONOMOUS_INCIDENT_SEND_JSON = ".autodev/autonomous_incident_send.json"
_DEFAULT_TARGET_FORMAT = "markdown"
_DEFAULT_WEBHOOK_TIMEOUT_SEC = 10.0
_DEFAULT_WEBHOOK_MAX_ATTEMPTS = 3
_DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC = 0.5
_DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER = 2.0
_DEFAULT_WEBHOOK_BACKOFF_MAX_SEC = 5.0
_DEFAULT_WEBHOOK_SIGNATURE_HEADER = "X-Autodev-Signature"
_DEFAULT_WEBHOOK_SECRET_ENV = "AUTODEV_INCIDENT_WEBHOOK_SECRET"
_DEFAULT_WEBHOOK_URL_ENV = "AUTODEV_INCIDENT_WEBHOOK_URL"

_REASON_DEDUPE_WINDOW = "incident_send.dedupe_window_active"
_REASON_RATE_LIMIT_GLOBAL = "incident_send.rate_limit_global"
_REASON_RATE_LIMIT_TARGET = "incident_send.rate_limit_target"
_REASON_FORCE_SEND_OVERRIDE = "incident_send.force_send_override"


@dataclass(frozen=True)
class IncidentSendTargetSpec:
    target: str
    export_format: str = _DEFAULT_TARGET_FORMAT


IncidentSendHandler = Callable[[dict[str, Any], str, bool, dict[str, Any]], Optional[dict[str, Any]]]


class WebhookSendError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


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


def _utc_now_iso(now_ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso_ts(value: Any) -> float | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token.endswith("Z"):
        token = token[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(token)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _coerce_non_negative_int(value: Any, default: int, *, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"incident send policy '{name}' must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"incident send policy '{name}' must be a non-negative integer") from e
    if parsed < 0:
        raise ValueError(f"incident send policy '{name}' must be >= 0")
    return parsed


def _coerce_optional_positive_int(value: Any, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"incident send policy '{name}' must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"incident send policy '{name}' must be a positive integer") from e
    if parsed <= 0:
        raise ValueError(f"incident send policy '{name}' must be > 0")
    return parsed


def _normalize_send_policy(raw_policy: dict[str, Any] | None) -> dict[str, Any]:
    cfg = raw_policy if isinstance(raw_policy, dict) else {}
    dedupe_window_sec = _coerce_non_negative_int(cfg.get("dedupe_window_sec"), 0, name="dedupe_window_sec")
    rate_limit_window_sec = _coerce_non_negative_int(cfg.get("rate_limit_window_sec"), 0, name="rate_limit_window_sec")
    global_max = _coerce_optional_positive_int(cfg.get("rate_limit_global_max"), name="rate_limit_global_max")
    per_target_max = _coerce_optional_positive_int(cfg.get("rate_limit_per_target_max"), name="rate_limit_per_target_max")
    force_send = bool(cfg.get("force_send", False))

    if rate_limit_window_sec == 0:
        global_max = None
        per_target_max = None

    return {
        "dedupe_window_sec": dedupe_window_sec,
        "rate_limit_window_sec": rate_limit_window_sec,
        "rate_limit_global_max": global_max,
        "rate_limit_per_target_max": per_target_max,
        "force_send": force_send,
    }


def _incident_fingerprint(packet: dict[str, Any]) -> str:
    canonical = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _count_historical_sent_attempts(
    history_attempts: list[dict[str, Any]],
    *,
    now_ts: float,
    window_sec: int,
) -> tuple[int, dict[str, int]]:
    if window_sec <= 0:
        return 0, {}

    cutoff = now_ts - float(window_sec)
    global_count = 0
    per_target: dict[str, int] = {}

    for item in history_attempts:
        if not isinstance(item, dict):
            continue
        if bool(item.get("dry_run")):
            continue
        decided_at_ts = _parse_iso_ts(item.get("decided_at"))
        if decided_at_ts is None or decided_at_ts < cutoff:
            continue
        attempts = item.get("attempts")
        if not isinstance(attempts, list):
            continue
        for row in attempts:
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") != "sent":
                continue
            target = str(row.get("target") or "").strip().lower() or "unknown"
            global_count += 1
            per_target[target] = per_target.get(target, 0) + 1

    return global_count, per_target


def _has_dedupe_hit(
    history_attempts: list[dict[str, Any]],
    *,
    fingerprint: str,
    now_ts: float,
    dedupe_window_sec: int,
) -> bool:
    if dedupe_window_sec <= 0:
        return False

    cutoff = now_ts - float(dedupe_window_sec)
    for item in history_attempts:
        if not isinstance(item, dict):
            continue
        if bool(item.get("dry_run")):
            continue
        if str(item.get("incident_fingerprint") or "") != fingerprint:
            continue
        decided_at_ts = _parse_iso_ts(item.get("decided_at"))
        if decided_at_ts is None or decided_at_ts < cutoff:
            continue
        attempts = item.get("attempts")
        if not isinstance(attempts, list):
            continue
        if any(isinstance(row, dict) and str(row.get("status") or "") == "sent" for row in attempts):
            return True
    return False


def send_incident_packet(
    *,
    run_dir: str | Path,
    targets: list[str] | None,
    dry_run: bool,
    trigger: str,
    target_configs: dict[str, Any] | None = None,
    send_policy: dict[str, Any] | None = None,
    history_attempts: list[dict[str, Any]] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    packet = load_incident_packet(run_path)
    specs = parse_incident_send_target_specs(targets)

    policy = _normalize_send_policy(send_policy)
    force_send = bool(policy.get("force_send"))
    now_epoch = float(now_ts if now_ts is not None else time.time())
    decided_at = _utc_now_iso(now_epoch)
    fingerprint = _incident_fingerprint(packet)

    history = [item for item in (history_attempts or []) if isinstance(item, dict)]
    suppression_events: list[dict[str, Any]] = []
    force_send_override_reasons: list[str] = []

    dedupe_hit = _has_dedupe_hit(
        history,
        fingerprint=fingerprint,
        now_ts=now_epoch,
        dedupe_window_sec=int(policy["dedupe_window_sec"]),
    )
    if dedupe_hit:
        suppression_events.append({"scope": "global", "reason_code": _REASON_DEDUPE_WINDOW})
        if force_send:
            force_send_override_reasons.append(_REASON_DEDUPE_WINDOW)

    historical_global_count, historical_per_target = _count_historical_sent_attempts(
        history,
        now_ts=now_epoch,
        window_sec=int(policy["rate_limit_window_sec"]),
    )
    projected_global_count = historical_global_count
    projected_per_target = dict(historical_per_target)

    attempts: list[dict[str, Any]] = []

    for spec in specs:
        rendered = render_incident_export(packet, spec.export_format)
        target_config = None
        if isinstance(target_configs, dict):
            target_config = target_configs.get(spec.target)

        attempt: dict[str, Any] = {
            "target": spec.target,
            "format": spec.export_format,
            "dry_run": dry_run,
        }

        suppress_reason_code = None
        if dedupe_hit:
            suppress_reason_code = _REASON_DEDUPE_WINDOW
        elif policy["rate_limit_global_max"] is not None and projected_global_count >= int(policy["rate_limit_global_max"]):
            suppress_reason_code = _REASON_RATE_LIMIT_GLOBAL
        elif policy["rate_limit_per_target_max"] is not None and projected_per_target.get(spec.target, 0) >= int(policy["rate_limit_per_target_max"]):
            suppress_reason_code = _REASON_RATE_LIMIT_TARGET

        if suppress_reason_code is not None:
            suppression_events.append({"scope": spec.target, "reason_code": suppress_reason_code})
            if force_send:
                force_send_override_reasons.append(suppress_reason_code)
            else:
                attempt["ok"] = True
                attempt["status"] = "suppressed"
                attempt["reason_code"] = suppress_reason_code
                attempt["details"] = {
                    "code": suppress_reason_code,
                    "decision": "suppressed",
                }
                attempts.append(attempt)
                continue

        handler = _INCIDENT_SEND_TARGETS[spec.target]
        if not dry_run:
            projected_global_count += 1
            projected_per_target[spec.target] = projected_per_target.get(spec.target, 0) + 1

        try:
            details = handler(
                packet,
                rendered,
                dry_run,
                {
                    "run_dir": str(run_path),
                    "trigger": trigger,
                    "target": spec.target,
                    "format": spec.export_format,
                    "target_config": target_config,
                    "incident_fingerprint": fingerprint,
                    "force_send": force_send,
                },
            )
            attempt["ok"] = True
            attempt["status"] = "dry_run" if dry_run else "sent"
            if isinstance(details, dict) and details:
                attempt["details"] = details
        except Exception as e:  # pragma: no cover - defensive guard
            attempt["ok"] = False
            attempt["status"] = "failed"
            attempt["error"] = str(e)
            details = getattr(e, "details", None)
            if isinstance(details, dict) and details:
                attempt["details"] = details
        attempts.append(attempt)

    suppressed_count = len([item for item in attempts if item.get("status") == "suppressed"])
    dry_run_count = len([item for item in attempts if item.get("status") == "dry_run"])
    sent_count = len([item for item in attempts if item.get("status") == "sent"])
    failure_count = len([item for item in attempts if item.get("status") == "failed"])

    sent_like_count = sent_count + dry_run_count
    attempt_count = len(attempts)
    if attempt_count > 0 and suppressed_count == attempt_count:
        aggregate_status = "suppressed"
    elif sent_like_count == 0:
        aggregate_status = "failed"
    elif failure_count == 0 and suppressed_count == 0 and sent_like_count == attempt_count:
        aggregate_status = "success"
    else:
        aggregate_status = "partial_success"

    ok = failure_count == 0
    suppression_reason_codes = sorted(
        {
            str(item.get("reason_code"))
            for item in attempts
            if isinstance(item, dict) and item.get("reason_code")
        }
    )

    forced_override_applied = bool(force_send_override_reasons)
    if forced_override_applied:
        suppression_reason_codes = sorted(set(suppression_reason_codes).union(set(force_send_override_reasons)))

    return {
        "schema_version": AUTONOMOUS_INCIDENT_SEND_VERSION,
        "trigger": trigger,
        "run_dir": str(run_path),
        "packet_status": str(packet.get("status") or "unknown"),
        "packet_run_id": str((packet.get("run_summary") or {}).get("run_id") or "-"),
        "incident_fingerprint": fingerprint,
        "decided_at": decided_at,
        "dry_run": dry_run,
        "targets": [f"{item.target}:{item.export_format}" for item in specs],
        "ok": ok,
        "aggregate_status": aggregate_status,
        "attempt_count": attempt_count,
        "success_count": len([item for item in attempts if item.get("ok") is True]),
        "failure_count": failure_count,
        "sent_count": sent_count,
        "dry_run_count": dry_run_count,
        "suppressed_count": suppressed_count,
        "suppressed": suppressed_count > 0,
        "suppression_reason_codes": suppression_reason_codes,
        "suppression_events": suppression_events,
        "send_policy": policy,
        "force_send": force_send,
        "force_send_override": {
            "applied": forced_override_applied,
            "reason_codes": sorted(set(force_send_override_reasons)),
            "code": _REASON_FORCE_SEND_OVERRIDE if forced_override_applied else None,
        },
        "per_target_outcomes": [
            {
                "target": item.get("target"),
                "format": item.get("format"),
                "status": item.get("status"),
                "ok": bool(item.get("ok") is True),
                "reason_code": item.get("reason_code"),
            }
            for item in attempts
            if isinstance(item, dict)
        ],
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


def _coerce_positive_float(value: Any, default: float, *, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise WebhookSendError(f"webhook config '{name}' must be a positive number", details={"code": "webhook_config_invalid", "field": name})
    try:
        parsed = float(value)
    except (TypeError, ValueError) as e:
        raise WebhookSendError(
            f"webhook config '{name}' must be a positive number",
            details={"code": "webhook_config_invalid", "field": name},
        ) from e
    if parsed <= 0:
        raise WebhookSendError(
            f"webhook config '{name}' must be > 0",
            details={"code": "webhook_config_invalid", "field": name},
        )
    return parsed


def _coerce_positive_int(value: Any, default: int, *, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise WebhookSendError(f"webhook config '{name}' must be a positive integer", details={"code": "webhook_config_invalid", "field": name})
    try:
        parsed = int(value)
    except (TypeError, ValueError) as e:
        raise WebhookSendError(
            f"webhook config '{name}' must be a positive integer",
            details={"code": "webhook_config_invalid", "field": name},
        ) from e
    if parsed <= 0:
        raise WebhookSendError(
            f"webhook config '{name}' must be > 0",
            details={"code": "webhook_config_invalid", "field": name},
        )
    return parsed


def _resolve_webhook_secret(cfg: dict[str, Any]) -> str:
    secret_env = str(cfg.get("signature_secret_env") or "").strip()
    if not secret_env:
        secret_env = _DEFAULT_WEBHOOK_SECRET_ENV

    secret_raw = cfg.get("signature_secret")
    if isinstance(secret_raw, str) and secret_raw.startswith("env:"):
        secret_env = secret_raw[len("env:") :].strip() or secret_env
        secret_raw = None

    if isinstance(secret_raw, str) and secret_raw:
        return secret_raw

    secret = os.getenv(secret_env)
    if secret:
        return secret

    raise WebhookSendError(
        "webhook signing secret is missing",
        details={
            "code": "webhook_secret_missing",
            "expected_env": secret_env,
            "hint": "set config.run.autonomous.incident_send.webhook.signature_secret or signature_secret_env",
        },
    )


def _resolve_webhook_config(context: dict[str, Any]) -> dict[str, Any]:
    raw_cfg = context.get("target_config")
    if raw_cfg is None:
        raw_cfg = {}
    if not isinstance(raw_cfg, dict):
        raise WebhookSendError(
            "incident-send webhook target config must be an object",
            details={"code": "webhook_config_invalid", "field": "target_config"},
        )

    url = str(raw_cfg.get("url") or os.getenv(_DEFAULT_WEBHOOK_URL_ENV) or "").strip()
    if not url:
        raise WebhookSendError(
            "webhook url is missing",
            details={
                "code": "webhook_url_missing",
                "expected_env": _DEFAULT_WEBHOOK_URL_ENV,
                "hint": "set config.run.autonomous.incident_send.webhook.url or AUTODEV_INCIDENT_WEBHOOK_URL",
            },
        )

    signature_header = str(raw_cfg.get("signature_header") or _DEFAULT_WEBHOOK_SIGNATURE_HEADER).strip()
    if not signature_header:
        raise WebhookSendError(
            "webhook config 'signature_header' must be non-empty",
            details={"code": "webhook_config_invalid", "field": "signature_header"},
        )

    timeout_sec = _coerce_positive_float(raw_cfg.get("timeout_sec"), _DEFAULT_WEBHOOK_TIMEOUT_SEC, name="timeout_sec")
    max_attempts = _coerce_positive_int(raw_cfg.get("max_attempts"), _DEFAULT_WEBHOOK_MAX_ATTEMPTS, name="max_attempts")
    backoff_initial_sec = _coerce_positive_float(
        raw_cfg.get("backoff_initial_sec"),
        _DEFAULT_WEBHOOK_BACKOFF_INITIAL_SEC,
        name="backoff_initial_sec",
    )
    backoff_multiplier = _coerce_positive_float(
        raw_cfg.get("backoff_multiplier"),
        _DEFAULT_WEBHOOK_BACKOFF_MULTIPLIER,
        name="backoff_multiplier",
    )
    backoff_max_sec = _coerce_positive_float(raw_cfg.get("backoff_max_sec"), _DEFAULT_WEBHOOK_BACKOFF_MAX_SEC, name="backoff_max_sec")
    secret = _resolve_webhook_secret(raw_cfg)

    return {
        "url": url,
        "signature_header": signature_header,
        "timeout_sec": timeout_sec,
        "max_attempts": max_attempts,
        "backoff_initial_sec": backoff_initial_sec,
        "backoff_multiplier": backoff_multiplier,
        "backoff_max_sec": backoff_max_sec,
        "secret": secret,
    }


def _post_webhook(*, url: str, body: bytes, headers: dict[str, str], timeout_sec: float) -> tuple[int, str]:
    req = urllib_request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
            status = int(getattr(resp, "status", 200))
            payload = resp.read().decode("utf-8", errors="replace")
            return status, payload
    except urllib_error.HTTPError as e:
        payload = ""
        try:
            payload = e.read().decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        return int(e.code), payload


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (urllib_error.URLError, TimeoutError, ConnectionError, OSError))


def _compute_backoff(attempt: int, *, initial_sec: float, multiplier: float, max_sec: float) -> float:
    power = max(attempt - 1, 0)
    wait = initial_sec * (multiplier**power)
    return min(wait, max_sec)


def _webhook_target(packet: dict[str, Any], rendered: str, dry_run: bool, context: dict[str, Any]) -> dict[str, Any]:
    cfg = _resolve_webhook_config(context)
    trigger = str(context.get("trigger") or "-")
    export_format = str(context.get("format") or _DEFAULT_TARGET_FORMAT)

    payload = {
        "event": "autonomous.incident_send",
        "schema_version": AUTONOMOUS_INCIDENT_SEND_VERSION,
        "trigger": trigger,
        "format": export_format,
        "run_summary": packet.get("run_summary") if isinstance(packet.get("run_summary"), dict) else {},
        "incident_packet": packet,
        "rendered": rendered,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(cfg["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
    signature_value = f"sha256={digest}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "autodev-incident-send/av3-009",
        cfg["signature_header"]: signature_value,
    }

    if dry_run:
        return {
            "target": "webhook",
            "url": cfg["url"],
            "signed": True,
            "signature_header": cfg["signature_header"],
            "attempts": [],
            "max_attempts": cfg["max_attempts"],
            "sent": False,
            "dry_run": True,
        }

    diagnostics: list[dict[str, Any]] = []
    max_attempts = int(cfg["max_attempts"])

    for attempt_index in range(1, max_attempts + 1):
        try:
            status_code, response_payload = _post_webhook(
                url=cfg["url"],
                body=body,
                headers=headers,
                timeout_sec=float(cfg["timeout_sec"]),
            )
            attempt_diag = {
                "attempt": attempt_index,
                "status_code": status_code,
                "ok": 200 <= status_code < 300,
                "retryable": _is_retryable_status(status_code),
            }
            if response_payload:
                attempt_diag["response_preview"] = response_payload[:240]
            diagnostics.append(attempt_diag)

            if 200 <= status_code < 300:
                return {
                    "target": "webhook",
                    "url": cfg["url"],
                    "signed": True,
                    "signature_header": cfg["signature_header"],
                    "status_code": status_code,
                    "attempts": diagnostics,
                    "sent": True,
                    "dry_run": False,
                }

            if not _is_retryable_status(status_code):
                raise WebhookSendError(
                    f"webhook request failed with non-retryable status: {status_code}",
                    details={
                        "code": "webhook_permanent_failure",
                        "status_code": status_code,
                        "attempts": diagnostics,
                    },
                )

            if attempt_index >= max_attempts:
                break
            backoff_sec = _compute_backoff(
                attempt_index,
                initial_sec=float(cfg["backoff_initial_sec"]),
                multiplier=float(cfg["backoff_multiplier"]),
                max_sec=float(cfg["backoff_max_sec"]),
            )
            diagnostics[-1]["backoff_sec"] = backoff_sec
            time.sleep(backoff_sec)
        except Exception as e:
            if isinstance(e, WebhookSendError):
                raise
            retryable = _is_retryable_exception(e)
            diag = {
                "attempt": attempt_index,
                "ok": False,
                "retryable": retryable,
                "error": str(e),
            }
            diagnostics.append(diag)

            if not retryable:
                raise WebhookSendError(
                    f"webhook request failed with non-retryable error: {e}",
                    details={"code": "webhook_permanent_failure", "attempts": diagnostics},
                ) from e

            if attempt_index >= max_attempts:
                break

            backoff_sec = _compute_backoff(
                attempt_index,
                initial_sec=float(cfg["backoff_initial_sec"]),
                multiplier=float(cfg["backoff_multiplier"]),
                max_sec=float(cfg["backoff_max_sec"]),
            )
            diagnostics[-1]["backoff_sec"] = backoff_sec
            time.sleep(backoff_sec)

    raise WebhookSendError(
        f"webhook request exhausted retries ({max_attempts} attempts)",
        details={"code": "webhook_transient_retry_exhausted", "attempts": diagnostics},
    )


register_incident_send_target("stdout", _stdout_target)
register_incident_send_target("log", _log_target)
register_incident_send_target("webhook", _webhook_target)
