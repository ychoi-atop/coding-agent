from __future__ import annotations

import json
from pathlib import Path
from typing import Any


XAI_DELIVERY_SCHEMA_VERSION = "av3-xai-v1"


def build_xai_delivery_packet(
    *,
    summary: str,
    repositories: list[dict[str, Any]],
    validation: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": XAI_DELIVERY_SCHEMA_VERSION,
        "summary": summary,
        "repositories": repositories,
        "validation": validation or {"status": "not_run", "notes": []},
        "artifacts": artifacts or [],
    }


def render_xai_delivery_packet(packet: dict[str, Any], *, output_format: str = "markdown") -> str:
    if output_format == "json":
        return json.dumps(packet, ensure_ascii=False, indent=2)

    repos = packet.get("repositories") if isinstance(packet.get("repositories"), list) else []
    lines = [
        "# XAI Delivery Packet",
        "",
        f"- Schema Version: `{packet.get('schema_version', XAI_DELIVERY_SCHEMA_VERSION)}`",
        f"- Summary: {packet.get('summary', '-')}",
        "",
        "## Repositories",
    ]
    if repos:
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            lines.append(f"### {repo.get('name', 'unknown-repo')}")
            lines.append("")
            capabilities = repo.get("xai_capabilities") if isinstance(repo.get("xai_capabilities"), list) else []
            endpoints = repo.get("endpoints") if isinstance(repo.get("endpoints"), list) else []
            files = repo.get("files") if isinstance(repo.get("files"), list) else []
            validations = repo.get("validations") if isinstance(repo.get("validations"), list) else []
            if capabilities:
                lines.append("Capabilities:")
                for item in capabilities:
                    lines.append(f"- {item}")
            if endpoints:
                lines.append("Endpoints:")
                for item in endpoints:
                    lines.append(f"- `{item}`")
            if files:
                lines.append("Files:")
                for item in files:
                    lines.append(f"- `{item}`")
            if validations:
                lines.append("Validations:")
                for item in validations:
                    lines.append(f"- {item}")
            lines.append("")
    else:
        lines.append("- none")

    validation = packet.get("validation") if isinstance(packet.get("validation"), dict) else {}
    lines.extend(
        [
            "## Validation",
            f"- Status: `{validation.get('status', 'unknown')}`",
        ]
    )
    notes = validation.get("notes") if isinstance(validation.get("notes"), list) else []
    for item in notes:
        lines.append(f"- {item}")

    artifacts = packet.get("artifacts") if isinstance(packet.get("artifacts"), list) else []
    lines.extend(["", "## Artifacts"])
    if artifacts:
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('label', '-')}: `{item.get('path', '-')}`")
    else:
        lines.append("- none")

    return "\n".join(lines)


def write_xai_delivery_packet(
    *,
    run_dir: str | Path,
    packet: dict[str, Any],
    output_format: str = "json",
) -> Path:
    run_path = Path(run_dir).expanduser().resolve()
    run_path.mkdir(parents=True, exist_ok=True)

    suffix = ".md" if output_format == "markdown" else ".json"
    target = run_path / f"xai_delivery_packet{suffix}"
    target.write_text(
        render_xai_delivery_packet(packet, output_format=output_format),
        encoding="utf-8",
    )
    return target
