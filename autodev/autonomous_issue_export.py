from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .autonomous_ticket_draft import (
    AUTONOMOUS_TICKET_DRAFT_JSON,
    build_autonomous_ticket_draft,
)
from .json_utils import json_dumps

AUTONOMOUS_ISSUE_EXPORT_JSON = ".autodev/autonomous_issue_export.json"
_AUTONOMOUS_ISSUE_EXPORT_SCHEMA_VERSION = "av3-013-v1"


CommandRunner = Callable[..., subprocess.CompletedProcess]
WhichFn = Callable[[str], Optional[str]]


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover - defensive parsing branch
        return None, f"invalid_json: {e}"
    if not isinstance(payload, dict):
        return None, "invalid_format: expected object"
    return payload, None


def _safe_str(value: Any, fallback: str = "-") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _make_issue_body(draft: dict[str, Any], run_dir: str) -> str:
    typed_codes = draft.get("typed_codes") if isinstance(draft.get("typed_codes"), list) else []
    repro_steps = draft.get("repro_steps") if isinstance(draft.get("repro_steps"), list) else []
    evidence = draft.get("evidence") if isinstance(draft.get("evidence"), list) else []
    next_actions = draft.get("suggested_next_actions") if isinstance(draft.get("suggested_next_actions"), list) else []

    lines = [
        "## AutoDev autonomous failure export",
        "",
        f"- Run directory: `{run_dir}`",
        f"- Severity: **{draft.get('severity', '-')}**",
        f"- Owner Team: **{draft.get('owner_team', '-')}**",
        f"- Target SLA: **{draft.get('target_sla', '-')}**",
        f"- Status: `{draft.get('status', '-')}`",
        f"- Failure Reason: `{draft.get('failure_reason', '-')}`",
        "",
        "## Failure Codes",
        f"- Typed Codes: {', '.join(str(item) for item in typed_codes) if typed_codes else '-'}",
        "",
        "## Reproduction Steps",
    ]
    if repro_steps:
        for idx, step in enumerate(repro_steps, start=1):
            lines.append(f"{idx}. {step}")
    else:
        lines.append("1. Reproduction details unavailable. Inspect autonomous artifacts directly.")

    lines.extend(["", "## Evidence"])
    if evidence:
        for item in evidence:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('label', '-')}: `{item.get('path', '-')}`")
    else:
        lines.append("- -")

    lines.extend(["", "## Suggested Next Actions"])
    if next_actions:
        for action in next_actions:
            lines.append(f"- {action}")
    else:
        lines.append("- -")

    return "\n".join(lines)


def _build_command_preview(repo: str, title: str) -> str:
    return " ".join(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            shlex.quote(repo),
            "--title",
            shlex.quote(title),
            "--body-file",
            "<autonomous_issue_export_body.md>",
        ]
    )


def build_github_issue_export_payload(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    draft_path = run_path / AUTONOMOUS_TICKET_DRAFT_JSON

    diagnostics: list[dict[str, str]] = []
    draft_payload, draft_error = _safe_load_json(draft_path)
    draft_source = "ticket_draft_json"
    if draft_payload is None:
        draft_payload = build_autonomous_ticket_draft(run_path)
        draft_source = "generated_fallback"
        diagnostics.append(
            {
                "level": "warning",
                "code": "issue_export.ticket_draft_missing_generated_fallback",
                "message": f"ticket draft json unavailable: {draft_error}; generated fallback draft from artifacts",
            }
        )

    title = _safe_str(draft_payload.get("title"), fallback="[AutoDev] Autonomous run issue export")
    body = _make_issue_body(draft_payload, str(run_path))

    return {
        "schema_version": _AUTONOMOUS_ISSUE_EXPORT_SCHEMA_VERSION,
        "mode": "autonomous_issue_export_payload_v1",
        "run_dir": str(run_path),
        "source": {
            "ticket_draft_json": {
                "path": str(draft_path),
                "status": "ok" if draft_error is None else draft_error,
            },
            "ticket_draft_source": draft_source,
        },
        "payload": {
            "title": title,
            "body": body,
        },
        "diagnostics": diagnostics,
    }


def export_github_issue(
    *,
    run_dir: str | Path,
    repo: str,
    dry_run: bool = True,
    command_runner: CommandRunner = subprocess.run,
    which: WhichFn = shutil.which,
) -> dict[str, Any]:
    export_payload = build_github_issue_export_payload(run_dir)
    issue_payload = export_payload.get("payload") if isinstance(export_payload.get("payload"), dict) else {}
    issue_title = _safe_str(issue_payload.get("title"), fallback="[AutoDev] Autonomous run issue export")
    issue_body = _safe_str(issue_payload.get("body"), fallback="AutoDev issue export body unavailable.")

    diagnostics = list(export_payload.get("diagnostics") or [])
    command_preview = _build_command_preview(repo, issue_title)

    result: dict[str, Any] = {
        "schema_version": _AUTONOMOUS_ISSUE_EXPORT_SCHEMA_VERSION,
        "mode": "autonomous_issue_export_v1",
        "attempted_at": _utc_now(),
        "run_dir": str(Path(run_dir).expanduser().resolve()),
        "repo": str(repo).strip(),
        "dry_run": bool(dry_run),
        "command_preview": command_preview,
        "payload": issue_payload,
        "source": export_payload.get("source"),
        "diagnostics": diagnostics,
        "status": "dry_run" if dry_run else "pending",
        "ok": True,
        "network_call_attempted": False,
    }

    if dry_run:
        return result

    gh_path = which("gh")
    if not gh_path:
        result["ok"] = False
        result["status"] = "blocked"
        result["diagnostics"].append(
            {
                "level": "error",
                "code": "issue_export.gh_cli_missing",
                "message": "GitHub CLI (`gh`) not found in PATH. Install gh or use --dry-run true.",
            }
        )
        return result

    auth_check = command_runner(
        [gh_path, "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if auth_check.returncode != 0:
        detail = (auth_check.stderr or auth_check.stdout or "").strip()
        result["ok"] = False
        result["status"] = "blocked"
        result["diagnostics"].append(
            {
                "level": "error",
                "code": "issue_export.gh_auth_missing",
                "message": "GitHub CLI auth is not ready (`gh auth status` failed). Run `gh auth login`.",
                "details": detail,
            }
        )
        return result

    create_result = command_runner(
        [gh_path, "issue", "create", "--repo", repo, "--title", issue_title, "--body", issue_body],
        capture_output=True,
        text=True,
        check=False,
    )
    result["network_call_attempted"] = True

    if create_result.returncode != 0:
        result["ok"] = False
        result["status"] = "failed"
        result["diagnostics"].append(
            {
                "level": "error",
                "code": "issue_export.gh_issue_create_failed",
                "message": "`gh issue create` failed.",
                "details": (create_result.stderr or create_result.stdout or "").strip(),
            }
        )
        return result

    stdout_text = (create_result.stdout or "").strip()
    issue_url = ""
    for line in stdout_text.splitlines():
        token = line.strip()
        if token.startswith("http://") or token.startswith("https://"):
            issue_url = token
            break
    if not issue_url and stdout_text:
        issue_url = stdout_text.splitlines()[-1].strip()

    result["status"] = "created"
    result["issue_url"] = issue_url
    result["gh_stdout"] = stdout_text
    return result


def persist_issue_export_attempt(run_dir: str | Path, attempt: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    run_path = Path(run_dir).expanduser().resolve()
    artifact_path = run_path / AUTONOMOUS_ISSUE_EXPORT_JSON
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    existing_payload, _ = _safe_load_json(artifact_path)
    attempts = []
    if isinstance(existing_payload, dict) and isinstance(existing_payload.get("attempts"), list):
        attempts = [item for item in existing_payload["attempts"] if isinstance(item, dict)]
    attempts.append(dict(attempt))

    payload = {
        "schema_version": _AUTONOMOUS_ISSUE_EXPORT_SCHEMA_VERSION,
        "mode": "autonomous_issue_export_v1",
        "updated_at": _utc_now(),
        "latest": attempt,
        "attempts": attempts,
    }
    artifact_path.write_text(json_dumps(payload), encoding="utf-8")
    return payload, artifact_path
