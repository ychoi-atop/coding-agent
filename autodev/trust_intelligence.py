from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .json_utils import json_dumps
from .xai_delivery_packet import build_xai_delivery_packet, write_xai_delivery_packet

TRUST_INTELLIGENCE_SCHEMA_VERSION = "av3-trust-v1"
TRUST_INTELLIGENCE_JSON = ".autodev/autonomous_trust_intelligence.json"
TRUST_INTELLIGENCE_MD = ".autodev/autonomous_trust_intelligence.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_status(value: Any) -> str:
    text = str(value or "").strip()
    return text or "missing"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"invalid_json: {exc}"
    if not isinstance(payload, dict):
        return None, "invalid_format: expected object"
    return payload, None


def _safe_load_jsonl(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], "missing"

    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                rows.append(payload)
    except Exception as exc:
        return [], f"invalid_jsonl: {exc}"
    return rows, None


def _sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_present_status(status: str) -> bool:
    return status in {"ok", "not_generated", "generated"}


def _score_band(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.6:
        return "moderate"
    return "low"


def _artifact_ref(name: str, details: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(details.get("path") or "")).expanduser()
    status = _safe_status(details.get("status"))
    return {
        "name": name,
        "path": str(path),
        "status": status,
        "sha256": _sha256_file(path) if status == "ok" else "",
    }


def _collect_artifact_refs(
    run_path: Path,
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    artifact_status = summary.get("artifacts")
    summary_artifacts = artifact_status if isinstance(artifact_status, dict) else {}

    refs = [
        _artifact_ref(name, details)
        for name, details in sorted(summary_artifacts.items())
        if isinstance(details, dict)
    ]

    extra_artifacts = {
        "run_metadata": {
            "path": run_path / ".autodev" / "run_metadata.json",
            "status": "ok" if (run_path / ".autodev" / "run_metadata.json").exists() else "missing",
        },
        "run_trace": {
            "path": run_path / ".autodev" / "run_trace.json",
            "status": "ok" if (run_path / ".autodev" / "run_trace.json").exists() else "missing",
        },
        "experiment_log": {
            "path": run_path / ".autodev" / "experiment_log.jsonl",
            "status": "ok" if (run_path / ".autodev" / "experiment_log.jsonl").exists() else "missing",
        },
    }
    refs.extend(
        _artifact_ref(name, details)
        for name, details in sorted(extra_artifacts.items())
    )
    return refs


def _derive_latest_quality(
    report_payload: Mapping[str, Any],
    experiment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    gate_results = report_payload.get("gate_results")
    if isinstance(gate_results, dict):
        gates = gate_results.get("gates")
        if isinstance(gates, dict):
            composite = gates.get("composite_quality")
            if isinstance(composite, dict):
                return {
                    "source": "report.gate_results.composite_quality",
                    "status": _safe_status(composite.get("status")),
                    "composite_score": composite.get("composite_score"),
                    "hard_blocked": bool(composite.get("hard_blocked")),
                    "components": _safe_dict(composite.get("components")),
                    "fail_reasons": [
                        str(item.get("code"))
                        for item in _safe_list(gate_results.get("fail_reasons"))
                        if isinstance(item, dict) and item.get("code")
                    ],
                }

    if experiment_rows:
        latest = experiment_rows[-1]
        decision = _safe_dict(latest.get("decision"))
        return {
            "source": "experiment_log.latest_entry",
            "status": _safe_status(decision.get("decision") or "unknown"),
            "composite_score": latest.get("composite_score"),
            "hard_blocked": bool(latest.get("hard_blocked")),
            "components": _safe_dict(latest.get("components")),
            "decision": decision,
            "validators_failed": [
                str(item) for item in _safe_list(latest.get("validators_failed")) if item
            ],
        }

    return {
        "source": "unavailable",
        "status": "unknown",
        "composite_score": None,
        "hard_blocked": None,
        "components": {},
    }


def _derive_runtime_observability(
    run_trace_payload: Mapping[str, Any],
    experiment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    events = _safe_list(run_trace_payload.get("events"))
    phases = _safe_list(run_trace_payload.get("phases"))
    llm_metrics = run_trace_payload.get("llm_metrics")
    llm_metric_rows = llm_metrics if isinstance(llm_metrics, dict) else {}

    llm_call_count = 0
    llm_retry_count = 0
    for row in llm_metric_rows.values():
        if not isinstance(row, dict):
            continue
        llm_call_count += int(row.get("call_count") or 0)
        llm_retry_count += int(row.get("retry_count") or 0)

    experiment_decisions = {"accepted": 0, "reverted": 0, "neutral": 0}
    for row in experiment_rows:
        decision = _safe_dict(row.get("decision"))
        key = str(decision.get("decision") or "").strip()
        if key in experiment_decisions:
            experiment_decisions[key] += 1

    return {
        "event_count": len(events),
        "phase_count": len(phases),
        "quality_score_events": len(
            [
                event
                for event in events
                if isinstance(event, dict)
                and str(event.get("event_type") or "") == "quality_score.computed"
            ]
        ),
        "experiment_decision_events": len(
            [
                event
                for event in events
                if isinstance(event, dict)
                and str(event.get("event_type") or "") == "experiment.decision"
            ]
        ),
        "llm_call_count": llm_call_count,
        "llm_retry_count": llm_retry_count,
        "experiment_entry_count": len(experiment_rows),
        "experiment_decisions": experiment_decisions,
    }


def _derive_evidence_integrity_signal(
    refs: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    by_name = {str(item.get("name")): item for item in refs}
    required = ["report", "gate_results", "guard_decisions", "run_trace"]
    if status == "failed":
        required.extend(["incident_packet", "ticket_draft_markdown", "ticket_draft_json"])
    recommended = ["run_metadata", "experiment_log", "strategy_trace"]

    missing_required = [
        name
        for name in required
        if not _is_present_status(_safe_status(_safe_dict(by_name.get(name)).get("status")))
    ]
    missing_recommended = [
        name
        for name in recommended
        if not _is_present_status(_safe_status(_safe_dict(by_name.get(name)).get("status")))
    ]

    total = len(required) if required else 1
    score = max(0.0, min(1.0, (total - len(missing_required)) / total))
    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
    }


def _derive_validation_signal(
    summary: Mapping[str, Any],
    latest_quality: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    status = str(summary.get("status") or "unknown")
    quality_status = str(latest_quality.get("status") or "unknown")
    hard_blocked = latest_quality.get("hard_blocked") is True

    if latest_quality.get("source") == "unavailable":
        score = 0.4
        reasons.append("latest_quality_signal_unavailable")
    elif hard_blocked:
        score = 0.2
        reasons.append("quality_signal_hard_blocked")
    elif status == "completed" and quality_status in {"passed", "accepted"}:
        score = 1.0
    elif status == "failed":
        score = 0.25
        reasons.append("run_failed")
    elif quality_status in {"advisory_warning", "neutral"}:
        score = 0.6
        reasons.append("quality_signal_advisory")
    else:
        score = 0.75

    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "latest_quality_status": quality_status,
        "reasons": reasons,
    }


def _derive_policy_traceability_signal(summary: Mapping[str, Any]) -> dict[str, Any]:
    status = str(summary.get("status") or "unknown")
    operator_guidance = _safe_dict(summary.get("operator_guidance"))
    incident_routing = _safe_dict(summary.get("incident_routing"))
    guidance_top = _safe_list(operator_guidance.get("top"))
    routing_primary = _safe_dict(incident_routing.get("primary"))
    guard_decision = summary.get("guard_decision")
    reasons: list[str] = []
    parts: list[float] = []

    if status != "completed":
        guard_present = isinstance(guard_decision, dict)
        parts.append(1.0 if guard_present else 0.0)
        if not guard_present:
            reasons.append("guard_decision_missing")

    guidance_present = len(guidance_top) > 0
    parts.append(1.0 if guidance_present else 0.0)
    if not guidance_present:
        reasons.append("operator_guidance_missing")

    routing_present = bool(routing_primary.get("owner_team"))
    parts.append(1.0 if routing_present else 0.0)
    if not routing_present:
        reasons.append("incident_routing_missing")

    score = sum(parts) / len(parts) if parts else 1.0
    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "reasons": reasons,
    }


def _derive_operator_readiness_signal(
    summary: Mapping[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str(summary.get("status") or "unknown")
    operator_guidance = _safe_dict(summary.get("operator_guidance"))
    guidance_top = _safe_list(operator_guidance.get("top"))
    by_name = {str(item.get("name")): item for item in refs}
    reasons: list[str] = []
    parts: list[float] = []

    parts.append(1.0 if guidance_top else 0.0)
    if not guidance_top:
        reasons.append("operator_guidance_top_missing")

    routing_ready = bool(str(summary.get("incident_owner_team") or "").strip() not in {"", "-"})
    parts.append(1.0 if routing_ready else 0.0)
    if not routing_ready:
        reasons.append("incident_owner_team_missing")

    if status == "failed":
        for name in ("incident_packet", "ticket_draft_markdown", "ticket_draft_json"):
            present = _is_present_status(
                _safe_status(_safe_dict(by_name.get(name)).get("status"))
            )
            parts.append(1.0 if present else 0.0)
            if not present:
                reasons.append(f"{name}_missing")

    score = sum(parts) / len(parts) if parts else 1.0
    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "reasons": reasons,
    }


def _derive_overall_trust_signal(components: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    scores = [
        float(_safe_dict(value).get("score") or 0.0)
        for value in components.values()
        if isinstance(value, Mapping)
    ]
    score = sum(scores) / len(scores) if scores else 0.0
    status = _score_band(score)
    requires_human_review = status != "high"
    if any(
        _safe_dict(value).get("status") == "low"
        for value in components.values()
        if isinstance(value, Mapping)
    ):
        requires_human_review = True
    return {
        "score": round(score, 2),
        "status": status,
        "requires_human_review": requires_human_review,
    }


def build_trust_intelligence_packet(
    run_dir: str | Path,
    *,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    artifacts_dir = run_path / ".autodev"

    report_payload, _ = _safe_load_json(artifacts_dir / "autonomous_report.json")
    run_trace_payload, _ = _safe_load_json(artifacts_dir / "run_trace.json")
    run_metadata_payload, _ = _safe_load_json(artifacts_dir / "run_metadata.json")
    experiment_rows, _ = _safe_load_jsonl(artifacts_dir / "experiment_log.jsonl")

    artifact_refs = _collect_artifact_refs(run_path, summary)
    latest_quality = _derive_latest_quality(_safe_dict(report_payload), experiment_rows)
    runtime_observability = _derive_runtime_observability(
        _safe_dict(run_trace_payload),
        experiment_rows,
    )

    evidence_integrity = _derive_evidence_integrity_signal(
        artifact_refs,
        str(summary.get("status") or "unknown"),
    )
    validation_signal = _derive_validation_signal(summary, latest_quality)
    policy_traceability = _derive_policy_traceability_signal(summary)
    operator_readiness = _derive_operator_readiness_signal(summary, artifact_refs)
    overall = _derive_overall_trust_signal(
        {
            "evidence_integrity": evidence_integrity,
            "validation_signal": validation_signal,
            "policy_traceability": policy_traceability,
            "operator_readiness": operator_readiness,
        }
    )

    operator_guidance = _safe_dict(summary.get("operator_guidance"))
    incident_routing = _safe_dict(summary.get("incident_routing"))
    top_guidance = _safe_list(operator_guidance.get("top"))
    primary_routing = _safe_dict(incident_routing.get("primary"))

    return {
        "schema_version": TRUST_INTELLIGENCE_SCHEMA_VERSION,
        "mode": "autonomous_v1_trust_intelligence",
        "generated_at": _utc_now(),
        "run_dir": str(run_path),
        "latest_run": _safe_dict(summary.get("latest_run")),
        "status": summary.get("status"),
        "summary_snapshot": {
            "preflight_status": summary.get("preflight_status"),
            "gate_counts": summary.get("gate_counts"),
            "dominant_fail_codes": summary.get("dominant_fail_codes"),
            "guard_decision": summary.get("guard_decision"),
            "operator_guidance_top": top_guidance,
            "incident_owner_team": summary.get("incident_owner_team"),
            "incident_severity": summary.get("incident_severity"),
            "incident_target_sla": summary.get("incident_target_sla"),
            "incident_escalation_class": summary.get("incident_escalation_class"),
            "warnings": summary.get("warnings"),
        },
        "artifacts": {
            "refs": artifact_refs,
            "total": len(artifact_refs),
            "ok_count": len([item for item in artifact_refs if item.get("status") == "ok"]),
        },
        "trust_signals": {
            "overall": overall,
            "evidence_integrity": evidence_integrity,
            "validation_signal": validation_signal,
            "policy_traceability": policy_traceability,
            "operator_readiness": operator_readiness,
        },
        "latest_quality": latest_quality,
        "runtime_observability": runtime_observability,
        "decision_trace": {
            "latest_strategy": summary.get("latest_strategy"),
            "guard_decision": summary.get("guard_decision"),
            "dominant_fail_codes": summary.get("dominant_fail_codes"),
            "operator_guidance_top": top_guidance,
            "incident_routing_primary": primary_routing,
        },
        "operator_next": {
            "owner_team": primary_routing.get("owner_team") or "-",
            "severity": primary_routing.get("severity") or "-",
            "target_sla": primary_routing.get("target_sla") or "-",
            "escalation_class": primary_routing.get("escalation_class") or "-",
            "top_actions": [
                {
                    "code": item.get("code"),
                    "title": item.get("title"),
                    "actions": _safe_list(item.get("actions")),
                    "playbook_url": item.get("playbook_url"),
                }
                for item in top_guidance[:3]
                if isinstance(item, dict)
            ],
        },
        "provenance": {
            "run_metadata": _safe_dict(run_metadata_payload),
            "run_trace_available": bool(run_trace_payload),
            "experiment_log_available": len(experiment_rows) > 0,
        },
        "warnings": [str(item) for item in _safe_list(summary.get("warnings")) if item],
    }


def build_xai_delivery_packet_from_trust(packet: Mapping[str, Any]) -> dict[str, Any]:
    latest_run = _safe_dict(packet.get("latest_run"))
    trust_signals = _safe_dict(packet.get("trust_signals"))
    overall = _safe_dict(trust_signals.get("overall"))
    latest_quality = _safe_dict(packet.get("latest_quality"))
    summary_snapshot = _safe_dict(packet.get("summary_snapshot"))
    operator_next = _safe_dict(packet.get("operator_next"))
    artifacts = _safe_dict(packet.get("artifacts"))
    refs = _safe_list(artifacts.get("refs"))

    repo_name = Path(str(packet.get("run_dir") or "")).name or "autodev-run"
    summary = (
        f"Run {latest_run.get('run_id') or '-'} trust={overall.get('status') or 'unknown'} "
        f"score={overall.get('score') or 0} status={packet.get('status') or 'unknown'}"
    )
    files = [str(item.get("path")) for item in refs if isinstance(item, dict) and item.get("status") == "ok"][:10]

    validations = [
        f"trust_status={overall.get('status') or 'unknown'}",
        f"evidence_integrity={_safe_dict(trust_signals.get('evidence_integrity')).get('status') or 'unknown'}",
        f"latest_quality_status={latest_quality.get('status') or 'unknown'}",
        f"incident_owner_team={operator_next.get('owner_team') or '-'}",
    ]

    packet_validation = {
        "status": "ready" if overall.get("requires_human_review") is False else "review_required",
        "notes": [
            f"overall_trust_score={overall.get('score') or 0}",
            f"gate_fail_count={_safe_dict(summary_snapshot.get('gate_counts')).get('fail') or 0}",
        ],
    }

    repo_payload = {
        "name": repo_name,
        "xai_capabilities": [
            "trust_intelligence_packet",
            "operator_guidance",
            "incident_routing",
            "quality_gate_summary",
            "run_trace_telemetry",
            "experiment_decision_log",
        ],
        "endpoints": [
            "autodev autonomous summary --run-dir <path>",
            "autodev autonomous triage-summary --run-dir <path>",
            "autodev autonomous trust-summary --run-dir <path>",
        ],
        "files": files,
        "validations": validations,
    }

    return build_xai_delivery_packet(
        summary=summary,
        repositories=[repo_payload],
        validation=packet_validation,
        artifacts=[
            {"label": str(item.get("name") or "-"), "path": str(item.get("path") or "-")}
            for item in refs[:10]
            if isinstance(item, dict)
        ],
    )


def render_trust_intelligence_packet(
    packet: Mapping[str, Any],
    *,
    output_format: str = "markdown",
) -> str:
    if output_format == "json":
        return json_dumps(dict(packet))

    trust_signals = _safe_dict(packet.get("trust_signals"))
    overall = _safe_dict(trust_signals.get("overall"))
    latest_quality = _safe_dict(packet.get("latest_quality"))
    operator_next = _safe_dict(packet.get("operator_next"))
    decision_trace = _safe_dict(packet.get("decision_trace"))
    guidance = _safe_list(operator_next.get("top_actions"))

    lines = [
        "# Autonomous Trust Intelligence",
        f"- run_dir: {packet.get('run_dir')}",
        f"- status: {packet.get('status')}",
        f"- trust_status: {overall.get('status', 'unknown')}",
        f"- trust_score: {overall.get('score', 0)}",
        f"- requires_human_review: {overall.get('requires_human_review', True)}",
        f"- latest_quality_source: {latest_quality.get('source', 'unavailable')}",
        f"- latest_quality_status: {latest_quality.get('status', 'unknown')}",
        f"- latest_quality_score: {latest_quality.get('composite_score', '-')}",
        f"- incident_owner_team: {operator_next.get('owner_team', '-')}",
        f"- incident_severity: {operator_next.get('severity', '-')}",
        f"- incident_target_sla: {operator_next.get('target_sla', '-')}",
        f"- incident_escalation_class: {operator_next.get('escalation_class', '-')}",
    ]

    for key in (
        "evidence_integrity",
        "validation_signal",
        "policy_traceability",
        "operator_readiness",
    ):
        signal = _safe_dict(trust_signals.get(key))
        lines.append(
            f"- {key}: {signal.get('status', 'unknown')} (score={signal.get('score', 0)})"
        )

    latest_strategy = decision_trace.get("latest_strategy")
    if isinstance(latest_strategy, dict):
        lines.append(f"- latest_strategy: {latest_strategy.get('name', '-')}")
    else:
        lines.append("- latest_strategy: -")

    guard_decision = decision_trace.get("guard_decision")
    if isinstance(guard_decision, dict):
        lines.append(
            f"- guard_decision: {guard_decision.get('decision', '-')} "
            f"({guard_decision.get('reason_code', '-')})"
        )
    else:
        lines.append("- guard_decision: -")

    if guidance:
        lines.append("- top_actions:")
        for item in guidance:
            if not isinstance(item, dict):
                continue
            actions = [
                str(action)
                for action in _safe_list(item.get("actions"))
                if action
            ]
            lines.append(
                f"  - {item.get('code', '-')}: {'; '.join(actions) or '-'}"
            )
    else:
        lines.append("- top_actions: -")

    return "\n".join(lines)


def persist_trust_intelligence_artifacts(
    run_dir: str | Path,
    packet: Mapping[str, Any],
) -> dict[str, str]:
    run_path = Path(run_dir).expanduser().resolve()
    artifacts_dir = run_path / ".autodev"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    trust_json = artifacts_dir / Path(TRUST_INTELLIGENCE_JSON).name
    trust_md = artifacts_dir / Path(TRUST_INTELLIGENCE_MD).name
    trust_json.write_text(json_dumps(dict(packet)), encoding="utf-8")
    trust_md.write_text(
        render_trust_intelligence_packet(packet, output_format="markdown"),
        encoding="utf-8",
    )

    xai_packet = build_xai_delivery_packet_from_trust(packet)
    xai_json = write_xai_delivery_packet(
        run_dir=artifacts_dir,
        packet=xai_packet,
        output_format="json",
    )
    xai_md = write_xai_delivery_packet(
        run_dir=artifacts_dir,
        packet=xai_packet,
        output_format="markdown",
    )

    return {
        "trust_json": str(trust_json),
        "trust_markdown": str(trust_md),
        "xai_json": str(xai_json),
        "xai_markdown": str(xai_md),
    }
