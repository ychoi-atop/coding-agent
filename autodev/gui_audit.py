from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_AUDIT_DIR = _REPO_ROOT / "artifacts" / "gui-audit"


def resolve_audit_dir(path: str | None = None) -> Path:
    if path and path.strip():
        candidate = Path(path.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = (_REPO_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate
    return _DEFAULT_AUDIT_DIR


def persist_audit_event(event: Mapping[str, Any], *, audit_dir: str | None = None) -> Path:
    target_dir = resolve_audit_dir(audit_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc)
    day = ts.strftime("%Y-%m-%d")
    target = target_dir / f"gui-audit-{day}.jsonl"

    payload: dict[str, Any] = {
        "timestamp": event.get("timestamp") or ts.isoformat(timespec="seconds"),
        **dict(event),
    }

    with target.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False))
        fp.write("\n")

    return target
