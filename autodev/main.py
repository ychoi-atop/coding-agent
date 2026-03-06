from __future__ import annotations

import argparse
import json
import logging
from uuid import uuid4
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .config import load_config
from .cli_progress import make_cli_progress_callback
from .llm_client import LLMClient, ModelEndpoint, ModelRouter
from .workspace import Workspace
from .loop import run_autodev_enterprise
from .report import write_report
from .json_utils import json_dumps

logger = logging.getLogger("autodev")

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}


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


def _coerce_optional_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be an integer, got {type(value).__name__}.")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be an integer, got {value!r}.")


def _coerce_max_parallel_tasks(value: Any, *, default: int = 2) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(
            f"config.run.max_parallel_tasks must be an integer between 1 and 3 (recommended), got {type(value).__name__}."
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.max_parallel_tasks must be an integer, got {value!r}.")
    if parsed < 1:
        raise SystemExit("config.run.max_parallel_tasks must be >= 1.")
    return parsed


def _coerce_role_temperatures(value: Any) -> Dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit("llm.role_temperatures must be an object map of role->temperature.")

    out: Dict[str, float] = {}
    for role_name, raw_temp in value.items():
        if isinstance(raw_temp, bool):
            raise SystemExit(f"llm.role_temperatures.{role_name} must be a number.")
        if isinstance(raw_temp, str):
            raw_temp = raw_temp.strip()
        try:
            temp = float(raw_temp)
        except (TypeError, ValueError) as e:
            raise SystemExit(f"llm.role_temperatures.{role_name} must be a number.") from e
        if temp < 0 or temp > 2:
            raise SystemExit(f"llm.role_temperatures.{role_name} must be between 0 and 2.")
        out[str(role_name)] = temp
    return out


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


def _cli_run(argv: list[str]) -> None:
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
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Resume from .autodev/checkpoint.json and skip already completed tasks.",
    )
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="Pause after plan generation and ask for confirmation before implementation starts.",
    )
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument(
        "--model",
        default=None,
        help="Override llm.model at runtime (highest precedence; then AUTODEV_LLM_MODEL env, then config).",
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (ValueError, OSError) as e:
        raise SystemExit(str(e)) from e

    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid config: 'profiles' must be an object.")

    profile_name = _resolve_profile_name(args.profile, profiles)
    prof = profiles[profile_name]
    run_cfg = cfg.get("run", {})
    if not isinstance(run_cfg, dict):
        raise SystemExit("Invalid config: 'run' must be an object.")

    budget_cfg = run_cfg.get("budget")
    if budget_cfg is not None and not isinstance(budget_cfg, dict):
        raise SystemExit("Invalid config: run.budget must be an object.")
    max_parallel_tasks = _coerce_max_parallel_tasks(run_cfg.get("max_parallel_tasks"), default=2)
    max_token_budget = None
    if isinstance(budget_cfg, dict):
        max_token_budget = _coerce_optional_int(budget_cfg.get("max_tokens"), "budget.max_tokens")
        if max_token_budget is not None and max_token_budget <= 0:
            raise SystemExit("config.run.budget.max_tokens must be a positive integer.")

    template_candidates = prof["template_candidates"]
    validators_enabled = prof["validators"]
    quality_profile = dict(prof.get("quality_profile", {}))
    per_task_soft = _coerce_optional_str_list(quality_profile.get("per_task_soft"))
    final_soft = _coerce_optional_str_list(quality_profile.get("final_soft"))
    disable_docker_build = bool(prof.get("disable_docker_build", False))

    prd_md = _read_text_file(args.prd, "PRD file")

    llm_cfg = cfg["llm"]
    role_temperatures = _coerce_role_temperatures(llm_cfg.get("role_temperatures"))
    llm_api_key = (llm_cfg.get("api_key") or "").strip()
    llm_oauth_token = (llm_cfg.get("oauth_token") or "").strip()
    if not llm_api_key and not llm_oauth_token:
        raise SystemExit(
            "Missing LLM authentication. Set llm.api_key or llm.oauth_token in config.yaml "
            "(or use ${AUTODEV_LLM_API_KEY}/${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}) "
            "or define AUTODEV_LLM_API_KEY/AUTODEV_CLAUDE_CODE_OAUTH_TOKEN in the environment."
        )

    llm_model = (args.model or os.getenv("AUTODEV_LLM_MODEL") or llm_cfg.get("model") or "").strip()
    if not llm_model:
        raise SystemExit(
            "Missing LLM model. Set llm.model in config.yaml, define AUTODEV_LLM_MODEL, or pass --model."
        )

    # Build optional multi-model router from llm.models + llm.role_mapping
    router: ModelRouter | None = None
    models_list = llm_cfg.get("models")
    if isinstance(models_list, list) and models_list:
        endpoints: list[ModelEndpoint] = []
        for entry in models_list:
            ep_api_key = (entry.get("api_key") or "").strip() or None
            ep_oauth = (entry.get("oauth_token") or "").strip() or None
            endpoints.append(
                ModelEndpoint(
                    base_url=entry["base_url"],
                    model=entry["model"],
                    api_key=ep_api_key,
                    oauth_token=ep_oauth,
                )
            )
        role_mapping_raw = llm_cfg.get("role_mapping")
        role_mapping_parsed: Dict[str, int] = {}
        if isinstance(role_mapping_raw, dict):
            role_mapping_parsed = {str(k): int(v) for k, v in role_mapping_raw.items()}
        router = ModelRouter(endpoints=endpoints, role_mapping=role_mapping_parsed)

    try:
        client = LLMClient(
            base_url=llm_cfg["base_url"],
            api_key=llm_api_key or None,
            oauth_token=llm_oauth_token or None,
            model=llm_model,
            timeout_sec=int(llm_cfg.get("timeout_sec", 240)),
            max_total_tokens=max_token_budget,
            router=router,
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
        "resume_requested": bool(args.resume),
        "interactive_requested": bool(args.interactive),
        "quality_profile": quality_profile,
        "template_candidates": template_candidates,
        "per_task_soft_validators": per_task_soft,
        "final_soft_validators": final_soft,
        "disable_docker_build": disable_docker_build,
        "validators_enabled": validators_enabled,
        "max_parallel_tasks": max_parallel_tasks,
        "max_parallel_tasks_recommended": 3,
        "resolved_from": quality_profile.get("name", "balanced"),
        "llm": {
            "model": llm_model,
            "auth_source": "api_key" if llm_api_key else "oauth_token",
            "model_override": {
                "cli": args.model,
                "env": os.getenv("AUTODEV_LLM_MODEL"),
            },
            "budget": {
                "max_total_tokens": max_token_budget,
            },
        },
        "role_temperatures": role_temperatures,
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
        llm_model=llm_model,
        llm_max_total_tokens=max_token_budget,
        role_temperatures=role_temperatures,
        max_parallel_tasks=max_parallel_tasks,
    )
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    import asyncio

    workflow_error: ValueError | None = None
    ok = False
    prd_struct: Dict[str, Any] = {}
    plan: Dict[str, Any] = {}
    _progress_cb = make_cli_progress_callback()
    last_validation: Any = []
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
                max_fix_loops_total=_coerce_int(run_cfg.get("max_fix_loops_total"), "max_fix_loops_total", 10),
                max_fix_loops_per_task=_coerce_int(run_cfg.get("max_fix_loops_per_task"), "max_fix_loops_per_task", 4),
                max_json_repair=_coerce_int(run_cfg.get("max_json_repair"), "max_json_repair", 2),
                task_soft_validators=per_task_soft,
                final_soft_validators=final_soft,
                quality_profile=quality_profile,
                disable_docker_build=disable_docker_build,
                verbose=bool(run_cfg.get("verbose", True)),
                run_id=run_id,
                request_id=request_id,
                profile=profile_name,
                resume=bool(args.resume),
                interactive=bool(args.interactive),
                role_temperatures=role_temperatures,
                max_parallel_tasks=max_parallel_tasks,
                progress_callback=_progress_cb,
            )
        )
    except ValueError as e:
        workflow_error = e

    llm_usage = client.usage_summary()
    run_metadata["llm_usage"] = llm_usage
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))
    _log_event(
        "autodev.run_cli_llm_usage",
        run_id=run_id,
        request_id=request_id,
        profile=profile_name,
        llm_usage=llm_usage,
    )

    if workflow_error is not None:
        run_metadata["result_ok"] = False
        run_metadata["run_completed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))
        _log_event(
            "autodev.run_cli_error",
            run_id=run_id,
            request_id=request_id,
            profile=profile_name,
            error=str(workflow_error),
        )
        raise SystemExit(
            "Workflow failed during structured generation or runtime validation: "
            f"{workflow_error}"
        ) from workflow_error

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
        llm_usage=llm_usage,
    )

    write_report(ws.root, prd_struct, plan, last_validation, ok)
    print({"ok": ok, "out": os.path.abspath(run_out), "llm_usage": llm_usage})
    if not ok:
        raise SystemExit(1)


def _cli_gui(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="autodev gui", description="Serve AutoDev GUI MVP")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8787, help="bind port (default: 8787)")
    ap.add_argument(
        "--runs-root",
        default="generated_runs",
        help="run directories root containing <run_id>/.autodev/* (default: generated_runs)",
    )
    args = ap.parse_args(argv)

    from .gui_mvp_server import serve

    runs_root = Path(args.runs_root).expanduser()
    if not runs_root.is_absolute():
        runs_root = Path.cwd() / runs_root
    serve(args.host, args.port, runs_root)


def _cli_local_simple(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="autodev local-simple",
        description="Run AutoDev GUI in local simple mode (localhost-first, single-user defaults)",
    )
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8787, help="bind port (default: 8787)")
    ap.add_argument(
        "--runs-root",
        default="generated_runs",
        help="run directories root containing <run_id>/.autodev/* (default: generated_runs)",
    )
    ap.add_argument(
        "--allow-non-localhost",
        action="store_true",
        help="allow binding local-simple mode to non-localhost host values",
    )
    ap.add_argument(
        "--role",
        default="developer",
        choices=["evaluator", "operator", "developer"],
        help="default GUI role used for local-simple mutating actions (default: developer)",
    )
    ap.add_argument(
        "--open",
        action="store_true",
        help="open the GUI URL in your default browser on startup (best-effort)",
    )
    args = ap.parse_args(argv)

    host = str(args.host).strip()
    if not args.allow_non_localhost and host not in _LOCALHOST_HOSTS:
        raise SystemExit(
            "local-simple mode is localhost-first. "
            "Use --host 127.0.0.1/localhost (or --allow-non-localhost to override)."
        )

    # Minimal-friction local defaults. Keep explicit user env values if already configured.
    os.environ.setdefault("AUTODEV_GUI_ROLE", str(args.role))
    os.environ.setdefault("AUTODEV_GUI_LOCAL_SIMPLE", "1")
    if "AUTODEV_GUI_AUTH_CONFIG" not in os.environ:
        os.environ["AUTODEV_GUI_AUTH_CONFIG"] = ""

    default_config = Path.cwd() / "config.yaml"
    if default_config.is_file():
        os.environ.setdefault("AUTODEV_GUI_DEFAULT_CONFIG", str(default_config.resolve()))

    default_prd = Path.cwd() / "examples" / "PRD.md"
    if default_prd.is_file():
        os.environ.setdefault("AUTODEV_GUI_DEFAULT_PRD", str(default_prd.resolve()))

    from .gui_mvp_server import serve

    runs_root = Path(args.runs_root).expanduser()
    if not runs_root.is_absolute():
        runs_root = Path.cwd() / runs_root

    gui_url = f"http://{host}:{args.port}"

    print("[gui-mvp] local-simple mode enabled")
    print("[gui-mvp] defaults: role=%s auth_config=%s" % (os.environ.get("AUTODEV_GUI_ROLE"), "disabled"))
    print(f"[gui-mvp] open: {gui_url}")
    if args.open:
        try:
            webbrowser.open(gui_url, new=2)
        except Exception as e:
            print(f"[gui-mvp] warning: failed to open browser automatically ({e})")
    serve(host, args.port, runs_root)


def cli(argv: list[str] | None = None) -> None:
    _configure_logging()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "gui":
        _cli_gui(raw_argv[1:])
        return
    if raw_argv and raw_argv[0] in {"local-simple", "local"}:
        _cli_local_simple(raw_argv[1:])
        return
    _cli_run(raw_argv)


if __name__ == "__main__":
    cli()
