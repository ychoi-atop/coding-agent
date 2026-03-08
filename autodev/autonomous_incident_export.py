from __future__ import annotations

import json
from pathlib import Path
from typing import Any

AUTONOMOUS_INCIDENT_PACKET_JSON = ".autodev/autonomous_incident_packet.json"
SUPPORTED_EXPORT_FORMATS = ("slack", "markdown", "email")


def load_incident_packet(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    packet_path = run_path / AUTONOMOUS_INCIDENT_PACKET_JSON

    if not packet_path.exists():
        raise FileNotFoundError(
            "incident packet not found: "
            f"{packet_path}\n"
            "Hint: incident packet is generated for failed autonomous runs. "
            "Run `autodev autonomous summary --run-dir <path>` to inspect run status."
        )

    try:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"incident packet is not valid JSON: {packet_path} ({e.msg})") from e

    if not isinstance(payload, dict):
        raise ValueError(f"incident packet payload must be an object: {packet_path}")

    return payload


def render_incident_export(packet: dict[str, Any], export_format: str) -> str:
    if export_format == "slack":
        return _render_slack(packet)
    if export_format == "markdown":
        return _render_markdown(packet)
    if export_format == "email":
        return _render_email(packet)
    raise ValueError(
        f"unsupported incident export format: {export_format} "
        f"(expected one of: {', '.join(SUPPORTED_EXPORT_FORMATS)})"
    )


def _safe_str(value: Any, fallback: str = "-") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _incident_view(packet: dict[str, Any]) -> dict[str, Any]:
    run_summary = packet.get("run_summary") if isinstance(packet.get("run_summary"), dict) else {}
    failure_codes = packet.get("failure_codes") if isinstance(packet.get("failure_codes"), dict) else {}
    incident_routing = packet.get("incident_routing") if isinstance(packet.get("incident_routing"), dict) else {}
    routing_primary = incident_routing.get("primary") if isinstance(incident_routing.get("primary"), dict) else {}
    reproduction = packet.get("reproduction") if isinstance(packet.get("reproduction"), dict) else {}
    artifact_paths = reproduction.get("artifact_paths") if isinstance(reproduction.get("artifact_paths"), dict) else {}
    operator_guidance = packet.get("operator_guidance") if isinstance(packet.get("operator_guidance"), dict) else {}
    top_actions = _safe_list(operator_guidance.get("top_actions"))
    retention_decisions = packet.get("retention_decisions") if isinstance(packet.get("retention_decisions"), dict) else {}
    retention_entries = _safe_list(retention_decisions.get("decisions"))
    retention_links = [
        str(item).strip()
        for item in _safe_list(retention_decisions.get("rationale_links"))
        if str(item).strip()
    ]

    typed_codes = [str(item).strip() for item in _safe_list(failure_codes.get("typed_codes")) if str(item).strip()]
    root_cause_codes = [str(item).strip() for item in _safe_list(failure_codes.get("root_cause_codes")) if str(item).strip()]

    return {
        "schema_version": _safe_str(packet.get("schema_version")),
        "status": _safe_str(packet.get("status")),
        "run_id": _safe_str(run_summary.get("run_id")),
        "request_id": _safe_str(run_summary.get("request_id")),
        "profile": _safe_str(run_summary.get("profile")),
        "failure_reason": _safe_str(run_summary.get("failure_reason")),
        "iterations_total": _safe_str(run_summary.get("iterations_total")),
        "iterations_failed": _safe_str(run_summary.get("iterations_failed")),
        "completed_at": _safe_str(run_summary.get("completed_at")),
        "typed_codes": typed_codes,
        "root_cause_codes": root_cause_codes,
        "owner_team": _safe_str(routing_primary.get("owner_team")),
        "severity": _safe_str(routing_primary.get("severity")),
        "target_sla": _safe_str(routing_primary.get("target_sla")),
        "escalation_class": _safe_str(routing_primary.get("escalation_class")),
        "reproduction_run_dir": _safe_str(reproduction.get("run_dir")),
        "artifact_paths": artifact_paths,
        "playbook": _safe_str(operator_guidance.get("playbook")),
        "top_actions": top_actions,
        "retention_decision_version": _safe_str(retention_decisions.get("decision_version")),
        "retention_decisions": retention_entries,
        "retention_rationale_links": retention_links,
        "generated_at": _safe_str(packet.get("generated_at")),
    }


def _render_top_actions_bullets(top_actions: list[Any], *, bullet: str) -> list[str]:
    if not top_actions:
        return [f"{bullet} -"]

    lines: list[str] = []
    for idx, entry in enumerate(top_actions, start=1):
        if not isinstance(entry, dict):
            continue
        code = _safe_str(entry.get("code"))
        title = _safe_str(entry.get("title"))
        action = _safe_str(entry.get("action"))
        playbook_url = _safe_str(entry.get("playbook_url"))
        lines.append(f"{bullet} {idx}. [{code}] {title}")
        lines.append(f"{bullet}    action: {action}")
        lines.append(f"{bullet}    playbook: {playbook_url}")

    return lines or [f"{bullet} -"]


def _render_artifacts_bullets(artifact_paths: dict[str, Any], *, bullet: str) -> list[str]:
    if not artifact_paths:
        return [f"{bullet} -"]

    lines: list[str] = []
    for key in sorted(artifact_paths.keys()):
        lines.append(f"{bullet} {key}: {_safe_str(artifact_paths.get(key))}")
    return lines


def _render_retention_decisions_bullets(entries: list[Any], *, bullet: str) -> list[str]:
    if not entries:
        return [f"{bullet} -"]

    lines: list[str] = []
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        category = _safe_str(entry.get("category"))
        decision = _safe_str(entry.get("decision"))
        rationale = _safe_str(entry.get("rationale"))
        lines.append(f"{bullet} {idx}. [{category}] {decision}")
        lines.append(f"{bullet}    rationale: {rationale}")

    return lines or [f"{bullet} -"]


def _render_link_bullets(links: list[str], *, bullet: str) -> list[str]:
    if not links:
        return [f"{bullet} -"]
    return [f"{bullet} {link}" for link in links]


def _render_slack(packet: dict[str, Any]) -> str:
    view = _incident_view(packet)
    typed_codes = ", ".join(view["typed_codes"]) if view["typed_codes"] else "-"
    root_codes = ", ".join(view["root_cause_codes"]) if view["root_cause_codes"] else "-"

    lines = [
        ":rotating_light: *AutoDev Incident Packet*",
        f"*Run:* {view['run_id']} (request {view['request_id']})",
        f"*Status:* {view['status']} | *Profile:* {view['profile']} | *Completed:* {view['completed_at']}",
        f"*Routing:* {view['owner_team']} | severity={view['severity']} | SLA={view['target_sla']} | escalation={view['escalation_class']}",
        f"*Failure reason:* {view['failure_reason']}",
        f"*Iterations:* total={view['iterations_total']}, failed={view['iterations_failed']}",
        f"*Typed codes:* {typed_codes}",
        f"*Root cause codes:* {root_codes}",
        "*Top operator actions:*",
        *_render_top_actions_bullets(view["top_actions"], bullet="•"),
        f"*Retention decisions:* version={view['retention_decision_version']}",
        *_render_retention_decisions_bullets(view["retention_decisions"], bullet="•"),
        "*Retention rationale links:*",
        *_render_link_bullets(view["retention_rationale_links"], bullet="•"),
        "*Reproduction:*",
        f"• run_dir: {view['reproduction_run_dir']}",
        "*Artifacts:*",
        *_render_artifacts_bullets(view["artifact_paths"], bullet="•"),
        f"*Playbook:* {view['playbook']}",
        f"*Packet:* schema={view['schema_version']} generated_at={view['generated_at']}",
    ]
    return "\n".join(lines)


def _render_markdown(packet: dict[str, Any]) -> str:
    view = _incident_view(packet)
    typed_codes = ", ".join(view["typed_codes"]) if view["typed_codes"] else "-"
    root_codes = ", ".join(view["root_cause_codes"]) if view["root_cause_codes"] else "-"

    lines = [
        "# Autonomous Incident Brief",
        "",
        "## Run Summary",
        f"- Run ID: `{view['run_id']}`",
        f"- Request ID: `{view['request_id']}`",
        f"- Status: `{view['status']}`",
        f"- Profile: `{view['profile']}`",
        f"- Completed At: `{view['completed_at']}`",
        f"- Failure Reason: `{view['failure_reason']}`",
        f"- Iterations: total=`{view['iterations_total']}`, failed=`{view['iterations_failed']}`",
        "",
        "## Incident Routing",
        f"- Owner Team: **{view['owner_team']}**",
        f"- Severity: **{view['severity']}**",
        f"- Target SLA: **{view['target_sla']}**",
        f"- Escalation Class: **{view['escalation_class']}**",
        "",
        "## Failure Codes",
        f"- Typed Codes: {typed_codes}",
        f"- Root Cause Codes: {root_codes}",
        "",
        "## Top Operator Actions",
        *_render_top_actions_bullets(view["top_actions"], bullet="-"),
        "",
        "## Retention / Compaction Decisions",
        f"- Decision Schema: `{view['retention_decision_version']}`",
        *_render_retention_decisions_bullets(view["retention_decisions"], bullet="-"),
        "- Rationale Links:",
        *_render_link_bullets(view["retention_rationale_links"], bullet="  -"),
        "",
        "## Reproduction",
        f"- Run Directory: `{view['reproduction_run_dir']}`",
        "- Artifacts:",
        *_render_artifacts_bullets(view["artifact_paths"], bullet="  -"),
        "",
        "## References",
        f"- Playbook: `{view['playbook']}`",
        f"- Packet Schema: `{view['schema_version']}`",
        f"- Generated At: `{view['generated_at']}`",
    ]
    return "\n".join(lines)


def _render_email(packet: dict[str, Any]) -> str:
    view = _incident_view(packet)
    typed_codes = ", ".join(view["typed_codes"]) if view["typed_codes"] else "-"
    root_codes = ", ".join(view["root_cause_codes"]) if view["root_cause_codes"] else "-"

    lines = [
        f"Subject: [AutoDev Incident] {view['run_id']} ({view['severity']})",
        "",
        "Team,",
        "",
        "An autonomous run failed and generated an incident packet.",
        "",
        "Run Summary",
        f"- Run ID: {view['run_id']}",
        f"- Request ID: {view['request_id']}",
        f"- Status: {view['status']}",
        f"- Profile: {view['profile']}",
        f"- Completed At: {view['completed_at']}",
        f"- Failure Reason: {view['failure_reason']}",
        f"- Iterations: total={view['iterations_total']}, failed={view['iterations_failed']}",
        "",
        "Routing",
        f"- Owner Team: {view['owner_team']}",
        f"- Severity: {view['severity']}",
        f"- Target SLA: {view['target_sla']}",
        f"- Escalation Class: {view['escalation_class']}",
        "",
        "Failure Codes",
        f"- Typed Codes: {typed_codes}",
        f"- Root Cause Codes: {root_codes}",
        "",
        "Top Actions",
        *_render_top_actions_bullets(view["top_actions"], bullet="-"),
        "",
        "Retention / Compaction Decisions",
        f"- Decision Schema: {view['retention_decision_version']}",
        *_render_retention_decisions_bullets(view["retention_decisions"], bullet="-"),
        "- Rationale Links:",
        *_render_link_bullets(view["retention_rationale_links"], bullet="  -"),
        "",
        "Reproduction",
        f"- Run Directory: {view['reproduction_run_dir']}",
        "- Artifacts:",
        *_render_artifacts_bullets(view["artifact_paths"], bullet="  -"),
        "",
        f"Playbook: {view['playbook']}",
        f"Packet Schema: {view['schema_version']}",
        f"Generated At: {view['generated_at']}",
    ]
    return "\n".join(lines)
