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


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, score))


def _normalize_percent_like(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1.0:
        numeric = numeric / 100.0
    return _clamp_score(numeric)


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
                    "normalized_composite_score": _normalize_percent_like(composite.get("composite_score")),
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
            "normalized_composite_score": _normalize_percent_like(latest.get("composite_score")),
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
        "normalized_composite_score": None,
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
    if status == "completed":
        required = ["report", "run_trace"]
        recommended = ["gate_results", "guard_decisions", "run_metadata", "experiment_log", "strategy_trace"]
    else:
        required = ["report", "gate_results", "guard_decisions", "run_trace"]
        recommended = ["run_metadata", "experiment_log", "strategy_trace"]
    if status == "failed":
        required.extend(["incident_packet", "ticket_draft_markdown", "ticket_draft_json"])

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

    required_total = len(required) if required else 1
    required_coverage = max(0.0, min(1.0, (required_total - len(missing_required)) / required_total))
    recommended_total = len(recommended) if recommended else 1
    recommended_coverage = max(0.0, min(1.0, (recommended_total - len(missing_recommended)) / recommended_total))
    score = _clamp_score((required_coverage * 0.8) + (recommended_coverage * 0.2))
    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "required_coverage": round(required_coverage, 2),
        "recommended_coverage": round(recommended_coverage, 2),
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
    }


def _derive_validation_signal(
    summary: Mapping[str, Any],
    latest_quality: Mapping[str, Any],
    experiment_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    evidence: list[dict[str, Any]] = []
    status = str(summary.get("status") or "unknown")
    quality_status = str(latest_quality.get("status") or "unknown")
    hard_blocked = latest_quality.get("hard_blocked") is True
    preflight_status = str(summary.get("preflight_status") or "unknown")
    gate_counts = _safe_dict(summary.get("gate_counts"))
    gate_total = max(0, int(gate_counts.get("total") or 0))
    gate_pass = max(0, int(gate_counts.get("pass") or 0))
    gate_pass_rate = (gate_pass / gate_total) if gate_total else 0.0
    normalized_quality = _normalize_percent_like(latest_quality.get("normalized_composite_score"))
    if normalized_quality is None:
        normalized_quality = _normalize_percent_like(latest_quality.get("composite_score"))

    accepted = 0
    reverted = 0
    for row in experiment_rows:
        decision = _safe_dict(row.get("decision"))
        decision_value = str(decision.get("decision") or "").strip().lower()
        if decision_value == "accepted":
            accepted += 1
        elif decision_value == "reverted":
            reverted += 1
    decision_total = accepted + reverted
    repeatability = accepted / decision_total if decision_total else (1.0 if status == "completed" else 0.5)

    score = 0.0
    quality_component = normalized_quality if normalized_quality is not None else 0.4
    score += quality_component * 0.55
    evidence.append({"factor": "quality_score", "weight": 0.55, "value": round(quality_component, 2)})

    gate_component = gate_pass_rate if gate_total else (1.0 if status == "completed" else 0.4)
    score += gate_component * 0.2
    evidence.append({"factor": "gate_pass_rate", "weight": 0.2, "value": round(gate_component, 2)})

    repeatability_component = repeatability
    score += repeatability_component * 0.15
    evidence.append({"factor": "repeatability", "weight": 0.15, "value": round(repeatability_component, 2)})

    source_component = 1.0 if latest_quality.get("source") != "unavailable" else 0.35
    score += source_component * 0.1
    evidence.append({"factor": "quality_source_available", "weight": 0.1, "value": round(source_component, 2)})

    if latest_quality.get("source") == "unavailable":
        reasons.append("latest_quality_signal_unavailable")
    if hard_blocked:
        score -= 0.4
        reasons.append("quality_signal_hard_blocked")
        evidence.append({"factor": "hard_block_penalty", "weight": -0.4, "value": 1.0})
    if status == "failed":
        score -= 0.25
        reasons.append("run_failed")
        evidence.append({"factor": "run_failed_penalty", "weight": -0.25, "value": 1.0})
    if preflight_status not in {"passed", "ok"}:
        score -= 0.15
        reasons.append(f"preflight_{preflight_status}")
        evidence.append({"factor": "preflight_penalty", "weight": -0.15, "value": 1.0})
    if quality_status in {"advisory_warning", "neutral", "soft_fail"}:
        score -= 0.1
        reasons.append("quality_signal_advisory")
        evidence.append({"factor": "advisory_penalty", "weight": -0.1, "value": 1.0})

    score = _clamp_score(score)

    return {
        "score": round(score, 2),
        "status": _score_band(score),
        "latest_quality_status": quality_status,
        "quality_score_normalized": round(normalized_quality, 2) if normalized_quality is not None else None,
        "gate_pass_rate": round(gate_pass_rate, 2) if gate_total else None,
        "repeatability": round(repeatability, 2),
        "evidence": evidence,
        "reasons": reasons,
    }


def _derive_policy_traceability_signal(summary: Mapping[str, Any]) -> dict[str, Any]:
    status = str(summary.get("status") or "unknown")
    operator_guidance = _safe_dict(summary.get("operator_guidance"))
    incident_routing = _safe_dict(summary.get("incident_routing"))
    guidance_top = _safe_list(operator_guidance.get("top"))
    routing_primary = _safe_dict(incident_routing.get("primary"))
    guard_decision = summary.get("guard_decision")
    preflight_status = str(summary.get("preflight_status") or "unknown")
    reasons: list[str] = []
    parts: list[float] = []

    preflight_present = preflight_status not in {"", "unknown", "missing"}
    parts.append(1.0 if preflight_present else 0.0)
    if not preflight_present:
        reasons.append("preflight_status_missing")

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

    severity_ready = bool(str(summary.get("incident_severity") or "").strip() not in {"", "-", "unknown"})
    parts.append(1.0 if severity_ready else 0.0)
    if not severity_ready:
        reasons.append("incident_severity_missing")

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


def _derive_overall_trust_signal(
    *,
    summary: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    latest_quality: Mapping[str, Any],
    runtime_observability: Mapping[str, Any],
) -> dict[str, Any]:
    weights = {
        "evidence_integrity": 0.25,
        "validation_signal": 0.4,
        "policy_traceability": 0.15,
        "operator_readiness": 0.2,
    }
    weighted_total = 0.0
    breakdown: list[dict[str, Any]] = []
    for key, weight in weights.items():
        component = _safe_dict(components.get(key))
        component_score = _clamp_score(float(component.get("score") or 0.0))
        weighted_total += component_score * weight
        breakdown.append(
            {
                "signal": key,
                "weight": weight,
                "score": round(component_score, 2),
                "weighted_score": round(component_score * weight, 2),
                "status": component.get("status"),
            }
        )

    diagnostics = _safe_list(summary.get("diagnostics"))
    warnings = _safe_list(summary.get("warnings"))
    event_count = int(runtime_observability.get("event_count") or 0)
    llm_call_count = int(runtime_observability.get("llm_call_count") or 0)
    quality_status = str(latest_quality.get("status") or "unknown")
    hard_blocked = latest_quality.get("hard_blocked") is True
    status = str(summary.get("status") or "unknown")
    preflight_status = str(summary.get("preflight_status") or "unknown")

    review_reasons: list[str] = []
    if preflight_status not in {"passed", "ok"}:
        review_reasons.append(f"preflight_status={preflight_status}")
    if hard_blocked:
        review_reasons.append("quality_gate_hard_blocked")
    if status == "failed":
        review_reasons.append("run_status=failed")
    evidence_integrity = _safe_dict(components.get("evidence_integrity"))
    missing_required = _safe_list(evidence_integrity.get("missing_required"))
    if missing_required:
        review_reasons.append(f"missing_required_artifacts={','.join(str(item) for item in missing_required)}")
    if diagnostics and status != "completed":
        review_reasons.append(f"diagnostics_present={len(diagnostics)}")
    if warnings and status != "completed":
        review_reasons.append(f"warnings_present={len(warnings)}")
    if event_count <= 0 and llm_call_count > 0:
        review_reasons.append("run_trace_events_missing")
    if quality_status not in {"passed", "accepted"}:
        review_reasons.append(f"latest_quality_status={quality_status}")

    score = _clamp_score(weighted_total)
    if diagnostics and status != "completed":
        score = _clamp_score(score - min(0.15, len(diagnostics) * 0.03))
    if warnings and status != "completed":
        score = _clamp_score(score - min(0.1, len(warnings) * 0.02))
    status_band = _score_band(score)
    requires_human_review = bool(review_reasons) or score < 0.85 or status_band != "high"

    explanation = "Autonomous approval-ready." if not requires_human_review else "Human review required because evidence or policy signals remain unresolved."
    return {
        "score": round(score, 2),
        "status": status_band,
        "requires_human_review": requires_human_review,
        "review_reasons": review_reasons,
        "explanation": explanation,
        "breakdown": breakdown,
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
    validation_signal = _derive_validation_signal(summary, latest_quality, experiment_rows)
    policy_traceability = _derive_policy_traceability_signal(summary)
    operator_readiness = _derive_operator_readiness_signal(summary, artifact_refs)
    component_signals = {
        "evidence_integrity": evidence_integrity,
        "validation_signal": validation_signal,
        "policy_traceability": policy_traceability,
        "operator_readiness": operator_readiness,
    }
    overall = _derive_overall_trust_signal(
        summary=summary,
        components=component_signals,
        latest_quality=latest_quality,
        runtime_observability=runtime_observability,
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
            "review_reasons": _safe_list(overall.get("review_reasons")),
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


def build_trust_summary(packet: Mapping[str, Any]) -> dict[str, Any]:
    trust_signals = _safe_dict(packet.get("trust_signals"))
    overall = _safe_dict(trust_signals.get("overall"))
    latest_quality = _safe_dict(packet.get("latest_quality"))
    operator_next = _safe_dict(packet.get("operator_next"))
    runtime_observability = _safe_dict(packet.get("runtime_observability"))

    return {
        "status": packet.get("status"),
        "trust_status": overall.get("status"),
        "trust_score": overall.get("score"),
        "requires_human_review": overall.get("requires_human_review"),
        "human_review_reasons": _safe_list(overall.get("review_reasons")),
        "trust_explanation": overall.get("explanation"),
        "latest_quality_status": latest_quality.get("status"),
        "latest_quality_score": latest_quality.get("composite_score"),
        "incident_owner_team": operator_next.get("owner_team"),
        "incident_severity": operator_next.get("severity"),
        "incident_target_sla": operator_next.get("target_sla"),
        "event_count": runtime_observability.get("event_count"),
        "llm_call_count": runtime_observability.get("llm_call_count"),
        "experiment_entry_count": runtime_observability.get("experiment_entry_count"),
    }


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
        f"- trust_explanation: {overall.get('explanation', '-')}",
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

    review_reasons = [str(item) for item in _safe_list(overall.get("review_reasons")) if item]
    if review_reasons:
        lines.append("- human_review_reasons:")
        for item in review_reasons:
            lines.append(f"  - {item}")
    else:
        lines.append("- human_review_reasons: -")

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
