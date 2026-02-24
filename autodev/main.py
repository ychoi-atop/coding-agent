from __future__ import annotations

import argparse
import json
import logging
from uuid import uuid4
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .llm_client import LLMClient
from .workspace import Workspace
from .loop import run_autodev_enterprise
from .report import write_report
from .json_utils import json_dumps

logger = logging.getLogger("autodev")


def _configure_logging() -> None:
    if logger.handlers:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        **fields,
    }
    if logger.handlers:
        logger.info(json_dumps(payload))
    else:
        print(json_dumps(payload))


def _coerce_optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, tuple):
        return [str(x) for x in value]
    return [str(value)]


def _slugify_prd_stem(prd_path: str) -> str:
    stem = Path(prd_path).stem.strip()
    if not stem:
        return "prd"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return slug or "prd"


def _resolve_output_dir(prd_path: str, out_root: str) -> str:
    prd_slug = _slugify_prd_stem(prd_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(out_root).expanduser()
    candidate = root / f"{prd_slug}_{ts}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{prd_slug}_{ts}_{suffix:02d}"
        suffix += 1
    return str(candidate)


def _read_text_file(path: str, label: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        raise SystemExit(f"{label} not found: {path}") from e
    except OSError as e:
        raise SystemExit(f"Unable to read {label}: {path} ({e})") from e


def _coerce_int(value: Any, key: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be an integer, got {type(value).__name__}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be an integer, got {value!r}.")


def _resolve_profile_name(requested: str | None, profiles: dict[str, Any]) -> str:
    if requested:
        if requested not in profiles:
            available = ", ".join(sorted(profiles))
            raise SystemExit(
                f"Profile '{requested}' not found. Available profiles: {available}"
            )
        return requested

    if len(profiles) == 1:
        return next(iter(profiles.keys()))

    available = ", ".join(sorted(profiles))
    raise SystemExit(
        "Profile was not provided and more than one profile is configured. "
        f"Available profiles: {available}."
    )


def cli():
    _configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--prd", required=True)
    ap.add_argument(
        "--out",
        required=True,
        help="Output root directory. A run folder named '<prd-file-stem>_<timestamp>' is created inside it.",
    )
    ap.add_argument(
        "--profile",
        default=None,
        help="Profile name from config.yaml. If omitted, a single available profile is used automatically.",
    )
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except (ValueError, OSError) as e:
        raise SystemExit(str(e)) from e

    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid config: 'profiles' must be an object.")

    profile_name = _resolve_profile_name(args.profile, profiles)
    prof = profiles[profile_name]
    template_candidates = prof["template_candidates"]
    validators_enabled = prof["validators"]
    quality_profile = dict(prof.get("quality_profile", {}))
    per_task_soft = _coerce_optional_str_list(quality_profile.get("per_task_soft"))
    final_soft = _coerce_optional_str_list(quality_profile.get("final_soft"))
    disable_docker_build = bool(prof.get("disable_docker_build", False))

    prd_md = _read_text_file(args.prd, "PRD file")

    llm_cfg = cfg["llm"]
    llm_api_key = (llm_cfg.get("api_key") or "").strip()
    if not llm_api_key:
        raise SystemExit(
            "Missing LLM API key. Set llm.api_key in config.yaml (or as ${AUTODEV_LLM_API_KEY}) "
            "or define AUTODEV_LLM_API_KEY in the environment."
        )
    try:
        client = LLMClient(
            base_url=llm_cfg["base_url"],
            api_key=llm_api_key,
            model=llm_cfg["model"],
            timeout_sec=int(llm_cfg.get("timeout_sec", 240)),
        )
    except (TypeError, ValueError) as e:
        raise SystemExit(f"Invalid llm config: {e}") from e

    run_out = _resolve_output_dir(args.prd, args.out)
    run_id = uuid4().hex
    request_id = uuid4().hex
    ws = Workspace(run_out)
    template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
    run_metadata = {
        "run_id": run_id,
        "request_id": request_id,
        "requested_profile": profile_name,
        "quality_profile": quality_profile,
        "template_candidates": template_candidates,
        "per_task_soft_validators": per_task_soft,
        "final_soft_validators": final_soft,
        "disable_docker_build": disable_docker_build,
        "validators_enabled": validators_enabled,
        "resolved_from": quality_profile.get("name", "balanced"),
        "quality_payload_files": {
            "task_quality_index": ".autodev/task_quality_index.json",
            "quality_profile": ".autodev/quality_profile.json",
            "quality_summary": ".autodev/quality_run_summary.json",
            "quality_resolution": ".autodev/quality_resolution.json",
            "final_last_validation": ".autodev/task_final_last_validation.json",
        },
    }
    _log_event(
        "autodev.run_cli_start",
        run_id=run_id,
        request_id=request_id,
        profile=profile_name,
        prd=args.prd,
        out_root=args.out,
        run_out=run_out,
    )
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    import asyncio

    try:
        ok, prd_struct, plan, last_validation = asyncio.run(
            run_autodev_enterprise(
                client=client,
                ws=ws,
                prd_markdown=prd_md,
                template_root=template_root,
                template_candidates=template_candidates,
                validators_enabled=validators_enabled,
                audit_required=bool(prof.get("security", {}).get("audit_required", False)),
                max_fix_loops_total=_coerce_int(cfg["run"].get("max_fix_loops_total"), "max_fix_loops_total", 10),
                max_fix_loops_per_task=_coerce_int(cfg["run"].get("max_fix_loops_per_task"), "max_fix_loops_per_task", 4),
                max_json_repair=_coerce_int(cfg["run"].get("max_json_repair"), "max_json_repair", 2),
                task_soft_validators=per_task_soft,
                final_soft_validators=final_soft,
                quality_profile=quality_profile,
                disable_docker_build=disable_docker_build,
                verbose=bool(cfg["run"].get("verbose", True)),
                run_id=run_id,
                request_id=request_id,
                profile=profile_name,
            )
        )
    except ValueError as e:
        _log_event(
            "autodev.run_cli_error",
            run_id=run_id,
            request_id=request_id,
            profile=profile_name,
            error=str(e),
        )
        raise SystemExit(f"Workflow failed during structured generation or runtime validation: {e}") from e

    quality_profile_path = os.path.join(run_out, ".autodev", "quality_profile.json")
    if os.path.exists(quality_profile_path):
        try:
            with open(quality_profile_path, "r", encoding="utf-8") as fp:
                resolved_quality_profile: dict[str, Any] = json.loads(fp.read())
            run_metadata["quality_profile"] = resolved_quality_profile
        except Exception:
            run_metadata["quality_profile"] = quality_profile
    else:
        run_metadata["quality_profile"] = quality_profile

    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    run_metadata["result_ok"] = ok
    run_metadata["run_completed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    _log_event(
        "autodev.run_cli_complete",
        run_id=run_id,
        request_id=request_id,
        profile=profile_name,
        ok=ok,
        output_dir=os.path.abspath(run_out),
        validators_enabled=validators_enabled,
    )

    write_report(ws.root, prd_struct, plan, last_validation, ok)
    print({"ok": ok, "out": os.path.abspath(run_out)})
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
