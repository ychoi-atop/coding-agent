from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from .cli_progress import make_cli_progress_callback
from .config import load_config
from .json_utils import json_dumps
from .llm_client import LLMClient, ModelEndpoint, ModelRouter
from .loop import run_autodev_enterprise
from .report import write_report
from .workspace import Workspace

logger = logging.getLogger("autodev")

AUTONOMOUS_STATE_FILE = ".autodev/autonomous_state.json"
AUTONOMOUS_REPORT_JSON = ".autodev/autonomous_report.json"
AUTONOMOUS_REPORT_MD = "AUTONOMOUS_REPORT.md"


@dataclass(frozen=True)
class AutonomousPolicy:
    max_iterations: int
    time_budget_sec: int
    workspace_allowlist: list[str]
    blocked_paths: list[str]
    allow_docker_build: bool
    allow_external_side_effects: bool


@dataclass(frozen=True)
class AutonomousTestsGateThresholds:
    min_pass_rate: float | None = None


@dataclass(frozen=True)
class AutonomousSecurityGateThresholds:
    max_high_findings: int | None = None


@dataclass(frozen=True)
class AutonomousPerformanceGateThresholds:
    max_regression_pct: float | None = None


@dataclass(frozen=True)
class AutonomousQualityGatePolicy:
    tests: AutonomousTestsGateThresholds | None = None
    security: AutonomousSecurityGateThresholds | None = None
    performance: AutonomousPerformanceGateThresholds | None = None


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _log_event(event: str, **fields: object) -> None:
    payload = {
        "ts": _utc_now(),
        "event": event,
        **fields,
    }
    if logger.handlers:
        logger.info(json_dumps(payload))
    else:
        print(json_dumps(payload))


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


def _coerce_optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, tuple):
        return [str(x) for x in value]
    return [str(value)]


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
            "config.run.max_parallel_tasks must be an integer between 1 and 3 (recommended), "
            f"got {type(value).__name__}."
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.max_parallel_tasks must be an integer, got {value!r}.")
    if parsed < 1:
        raise SystemExit("config.run.max_parallel_tasks must be >= 1.")
    return parsed


def _coerce_optional_float(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"config.run.{key} must be a number, got {type(value).__name__}.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"config.run.{key} must be a number, got {value!r}.")


def _resolve_autonomous_quality_gate_policy(run_cfg: dict[str, Any]) -> AutonomousQualityGatePolicy | None:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is None:
        return None
    if not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")

    raw_policy = auto_cfg.get("quality_gate_policy")
    if raw_policy is None:
        return None
    if not isinstance(raw_policy, dict):
        raise SystemExit("config.run.autonomous.quality_gate_policy must be an object when set.")

    tests_thresholds = None
    raw_tests = raw_policy.get("tests")
    if raw_tests is not None:
        if not isinstance(raw_tests, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.tests must be an object.")
        min_pass_rate = _coerce_optional_float(
            raw_tests.get("min_pass_rate"),
            "autonomous.quality_gate_policy.tests.min_pass_rate",
        )
        if min_pass_rate is not None and (min_pass_rate < 0 or min_pass_rate > 1):
            raise SystemExit("config.run.autonomous.quality_gate_policy.tests.min_pass_rate must be between 0 and 1.")
        tests_thresholds = AutonomousTestsGateThresholds(min_pass_rate=min_pass_rate)

    security_thresholds = None
    raw_security = raw_policy.get("security")
    if raw_security is not None:
        if not isinstance(raw_security, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.security must be an object.")
        max_high_findings = _coerce_optional_int(
            raw_security.get("max_high_findings"),
            "autonomous.quality_gate_policy.security.max_high_findings",
        )
        if max_high_findings is not None and max_high_findings < 0:
            raise SystemExit("config.run.autonomous.quality_gate_policy.security.max_high_findings must be >= 0.")
        security_thresholds = AutonomousSecurityGateThresholds(max_high_findings=max_high_findings)

    performance_thresholds = None
    raw_performance = raw_policy.get("performance")
    if raw_performance is not None:
        if not isinstance(raw_performance, dict):
            raise SystemExit("config.run.autonomous.quality_gate_policy.performance must be an object.")
        max_regression_pct = _coerce_optional_float(
            raw_performance.get("max_regression_pct"),
            "autonomous.quality_gate_policy.performance.max_regression_pct",
        )
        if max_regression_pct is not None and max_regression_pct < 0:
            raise SystemExit("config.run.autonomous.quality_gate_policy.performance.max_regression_pct must be >= 0.")
        performance_thresholds = AutonomousPerformanceGateThresholds(max_regression_pct=max_regression_pct)

    return AutonomousQualityGatePolicy(
        tests=tests_thresholds,
        security=security_thresholds,
        performance=performance_thresholds,
    )


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
            raise SystemExit(f"Profile '{requested}' not found. Available profiles: {available}")
        return requested

    if len(profiles) == 1:
        return next(iter(profiles.keys()))

    available = ", ".join(sorted(profiles))
    raise SystemExit(
        "Profile was not provided and more than one profile is configured. "
        f"Available profiles: {available}."
    )


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _enforce_path_boundaries(policy: AutonomousPolicy, *, prd: str, config: str, out_root: str, run_out: str) -> None:
    allowlist = [_normalize_path(x) for x in policy.workspace_allowlist]
    blocked = [_normalize_path(x) for x in policy.blocked_paths]
    targets = {
        "prd": _normalize_path(prd),
        "config": _normalize_path(config),
        "out_root": _normalize_path(out_root),
        "run_out": _normalize_path(run_out),
    }

    for label, target in targets.items():
        if not any(_is_under(target, root) for root in allowlist):
            raise SystemExit(
                f"Autonomous policy blocked path '{label}': {target}. "
                f"Not under workspace_allowlist={allowlist}."
            )
        if any(_is_under(target, blocked_root) for blocked_root in blocked):
            raise SystemExit(
                f"Autonomous policy blocked path '{label}': {target}. "
                f"Matches blocked_paths={blocked}."
            )


def _read_text_file(path: str, label: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError as e:
        raise SystemExit(f"{label} not found: {path}") from e
    except OSError as e:
        raise SystemExit(f"Unable to read {label}: {path} ({e})") from e


def _resolve_autonomous_policy(args: argparse.Namespace, run_cfg: dict[str, Any]) -> AutonomousPolicy:
    auto_cfg = run_cfg.get("autonomous")
    if auto_cfg is not None and not isinstance(auto_cfg, dict):
        raise SystemExit("config.run.autonomous must be an object when set.")
    auto_cfg = auto_cfg or {}

    max_iterations_cfg = auto_cfg.get("max_iterations", 3)
    max_iterations = int(args.max_iterations if args.max_iterations is not None else max_iterations_cfg)
    if max_iterations <= 0:
        raise SystemExit("autonomous max_iterations must be >= 1")

    time_budget_cfg = auto_cfg.get("time_budget_sec", 3600)
    time_budget_sec = int(args.time_budget_sec if args.time_budget_sec is not None else time_budget_cfg)
    if time_budget_sec <= 0:
        raise SystemExit("autonomous time_budget_sec must be >= 1")

    default_allowlist = [str(Path.cwd().resolve())]
    allowlist = args.workspace_allowlist or auto_cfg.get("workspace_allowlist") or default_allowlist
    if not isinstance(allowlist, list) or not allowlist:
        raise SystemExit("autonomous workspace_allowlist must be a non-empty list")

    blocked = args.blocked_paths
    if blocked is None:
        blocked = auto_cfg.get("blocked_paths")
    blocked = blocked or []
    if not isinstance(blocked, list):
        raise SystemExit("autonomous blocked_paths must be a list")

    side_effect_cfg = auto_cfg.get("external_side_effects")
    if side_effect_cfg is not None and not isinstance(side_effect_cfg, dict):
        raise SystemExit("config.run.autonomous.external_side_effects must be an object")
    side_effect_cfg = side_effect_cfg or {}

    allow_docker_build = bool(
        args.allow_docker_build
        if args.allow_docker_build is not None
        else side_effect_cfg.get("allow_docker_build", False)
    )
    allow_external_side_effects = bool(
        args.allow_external_side_effects
        if args.allow_external_side_effects is not None
        else side_effect_cfg.get("allow_external_side_effects", False)
    )

    return AutonomousPolicy(
        max_iterations=max_iterations,
        time_budget_sec=time_budget_sec,
        workspace_allowlist=[str(x) for x in allowlist],
        blocked_paths=[str(x) for x in blocked],
        allow_docker_build=allow_docker_build,
        allow_external_side_effects=allow_external_side_effects,
    )


def _new_state(
    *,
    run_id: str,
    request_id: str,
    run_out: str,
    profile: str,
    policy: AutonomousPolicy,
    quality_gate_policy: AutonomousQualityGatePolicy | None,
    prd_path: str,
    config_path: str,
) -> dict[str, Any]:
    policy_payload: dict[str, Any] = {
        "max_iterations": policy.max_iterations,
        "time_budget_sec": policy.time_budget_sec,
        "workspace_allowlist": [_normalize_path(x) for x in policy.workspace_allowlist],
        "blocked_paths": [_normalize_path(x) for x in policy.blocked_paths],
        "allow_docker_build": policy.allow_docker_build,
        "allow_external_side_effects": policy.allow_external_side_effects,
    }
    if quality_gate_policy is not None:
        policy_payload["quality_gate_policy"] = asdict(quality_gate_policy)

    return {
        "version": 1,
        "mode": "autonomous_v1",
        "status": "running",
        "phase": "ingest",
        "run_id": run_id,
        "request_id": request_id,
        "profile": profile,
        "run_out": os.path.abspath(run_out),
        "prd": os.path.abspath(prd_path),
        "config": os.path.abspath(config_path),
        "policy": policy_payload,
        "current_iteration": 0,
        "attempts": [],
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _load_state(ws: Workspace) -> dict[str, Any] | None:
    if not ws.exists(AUTONOMOUS_STATE_FILE):
        return None
    try:
        raw = ws.read_text(AUTONOMOUS_STATE_FILE)
        payload = json.loads(raw)
    except Exception as e:
        raise SystemExit(f"Failed to load autonomous state: {e}") from e
    if not isinstance(payload, dict):
        raise SystemExit("Invalid autonomous state format: expected JSON object")
    return payload


def _write_state(ws: Workspace, state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    ws.write_text(AUTONOMOUS_STATE_FILE, json_dumps(state))


def _render_report(state: dict[str, Any], *, ok: bool, last_validation: Any) -> tuple[dict[str, Any], str]:
    attempts = state.get("attempts") if isinstance(state.get("attempts"), list) else []
    report = {
        "mode": "autonomous_v1",
        "ok": ok,
        "run_id": state.get("run_id"),
        "request_id": state.get("request_id"),
        "run_out": state.get("run_out"),
        "profile": state.get("profile"),
        "iterations_total": len(attempts),
        "iterations_ok": len([a for a in attempts if isinstance(a, dict) and a.get("ok") is True]),
        "iterations_failed": len([a for a in attempts if isinstance(a, dict) and a.get("ok") is False]),
        "policy": state.get("policy"),
        "last_validation": last_validation,
        "attempts": attempts,
        "completed_at": _utc_now(),
    }
    md = [
        "# Autonomous Mode Report",
        "",
        f"- Result: {'OK' if ok else 'FAILED'}",
        f"- Run ID: `{state.get('run_id', '')}`",
        f"- Request ID: `{state.get('request_id', '')}`",
        f"- Run directory: `{state.get('run_out', '')}`",
        f"- Profile: `{state.get('profile', '')}`",
        f"- Iterations: `{len(attempts)}`",
        "",
        "## Attempts",
    ]
    for item in attempts:
        if not isinstance(item, dict):
            continue
        md.append(
            f"- Iteration {item.get('iteration')}: "
            f"`{'OK' if item.get('ok') else 'FAILED'}` "
            f"(resume={item.get('resume')}, reason={item.get('reason', '-')})"
        )
    md.append("")
    return report, "\n".join(md)


def _build_cli_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="autodev autonomous", description="AutoDev fully autonomous mode")
    sub = ap.add_subparsers(dest="action", required=True)

    start = sub.add_parser("start", help="start or resume unattended autonomous run")
    start.add_argument("--prd", required=True)
    start.add_argument(
        "--out",
        required=True,
        help="Output root directory. A run folder named '<prd-file-stem>_<timestamp>' is created inside it.",
    )
    start.add_argument("--profile", default=None)
    start.add_argument("--config", default="config.yaml")
    start.add_argument("--model", default=None)
    start.add_argument("--resume", action="store_true", help="Resume normal run checkpoint on first autonomous iteration")
    start.add_argument(
        "--resume-state",
        action="store_true",
        help="Resume from existing autonomous state in --run-dir (or inferred run directory)",
    )
    start.add_argument(
        "--run-dir",
        default="",
        help="Existing run directory to resume autonomous state from (must contain .autodev/autonomous_state.json)",
    )
    start.add_argument("--max-iterations", type=int, default=None)
    start.add_argument("--time-budget-sec", type=int, default=None)
    start.add_argument("--workspace-allowlist", action="append", default=None)
    start.add_argument("--blocked-paths", action="append", default=None)
    start.add_argument("--allow-docker-build", action="store_true", default=None)
    start.add_argument("--allow-external-side-effects", action="store_true", default=None)

    status = sub.add_parser("status", help="print autonomous state for a run")
    status.add_argument("--run-dir", required=True)

    return ap


def _start(argv: list[str]) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(["start", *argv])

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

    policy = _resolve_autonomous_policy(args, run_cfg)
    quality_gate_policy = _resolve_autonomous_quality_gate_policy(run_cfg)

    run_out = str(Path(args.run_dir).expanduser().resolve()) if args.run_dir else _resolve_output_dir(args.prd, args.out)
    ws = Workspace(run_out)

    if args.resume_state:
        loaded = _load_state(ws)
        if loaded is None:
            raise SystemExit(f"--resume-state requested, but state file missing: {run_out}/{AUTONOMOUS_STATE_FILE}")
        state = loaded
        run_id = str(state.get("run_id") or uuid4().hex)
        request_id = str(state.get("request_id") or uuid4().hex)
    else:
        run_id = uuid4().hex
        request_id = uuid4().hex
        state = _new_state(
            run_id=run_id,
            request_id=request_id,
            run_out=run_out,
            profile=profile_name,
            policy=policy,
            quality_gate_policy=quality_gate_policy,
            prd_path=args.prd,
            config_path=args.config,
        )

    _enforce_path_boundaries(policy, prd=args.prd, config=args.config, out_root=args.out, run_out=run_out)

    template_candidates = prof["template_candidates"]
    validators_enabled = prof["validators"]
    quality_profile = dict(prof.get("quality_profile", {}))
    per_task_soft = _coerce_optional_str_list(quality_profile.get("per_task_soft"))
    final_soft = _coerce_optional_str_list(quality_profile.get("final_soft"))
    disable_docker_build = bool(prof.get("disable_docker_build", False)) or (not policy.allow_docker_build)

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

    max_parallel_tasks = _coerce_max_parallel_tasks(run_cfg.get("max_parallel_tasks"), default=2)
    max_token_budget = None
    if isinstance(budget_cfg, dict):
        max_token_budget = _coerce_optional_int(budget_cfg.get("max_tokens"), "budget.max_tokens")
        if max_token_budget is not None and max_token_budget <= 0:
            raise SystemExit("config.run.budget.max_tokens must be a positive integer.")

    client = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=llm_api_key or None,
        oauth_token=llm_oauth_token or None,
        model=llm_model,
        timeout_sec=int(llm_cfg.get("timeout_sec", 240)),
        max_total_tokens=max_token_budget,
        router=router,
    )

    template_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))

    run_metadata = {
        "run_id": run_id,
        "request_id": request_id,
        "requested_profile": profile_name,
        "autonomous_mode": True,
        "autonomous_policy": {
            "max_iterations": policy.max_iterations,
            "time_budget_sec": policy.time_budget_sec,
            "workspace_allowlist": [_normalize_path(x) for x in policy.workspace_allowlist],
            "blocked_paths": [_normalize_path(x) for x in policy.blocked_paths],
            "allow_docker_build": policy.allow_docker_build,
            "allow_external_side_effects": policy.allow_external_side_effects,
        },
        "quality_profile": quality_profile,
        "template_candidates": template_candidates,
        "per_task_soft_validators": per_task_soft,
        "final_soft_validators": final_soft,
        "disable_docker_build": disable_docker_build,
        "validators_enabled": validators_enabled,
        "max_parallel_tasks": max_parallel_tasks,
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
    }
    if quality_gate_policy is not None:
        run_metadata["autonomous_quality_gate_policy"] = asdict(quality_gate_policy)
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))
    _write_state(ws, state)

    start_monotonic = time.monotonic()
    ok = False
    prd_struct: Dict[str, Any] = {}
    plan: Dict[str, Any] = {}
    last_validation: Any = []

    attempt_index = int(state.get("current_iteration", 0))
    explicit_resume_first = bool(args.resume)
    while attempt_index < policy.max_iterations:
        elapsed = int(time.monotonic() - start_monotonic)
        if elapsed >= policy.time_budget_sec:
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "time_budget_exceeded"
            _write_state(ws, state)
            break

        attempt_index += 1
        state["current_iteration"] = attempt_index
        state["phase"] = "plan"
        _write_state(ws, state)

        resume_flag = explicit_resume_first if attempt_index == 1 else True
        _log_event(
            "autonomous.iteration_start",
            run_id=run_id,
            request_id=request_id,
            profile=profile_name,
            run_out=run_out,
            iteration=attempt_index,
            resume=resume_flag,
        )

        attempt_record: dict[str, Any] = {
            "iteration": attempt_index,
            "started_at": _utc_now(),
            "resume": resume_flag,
        }

        workflow_error: ValueError | None = None
        try:
            state["phase"] = "execute"
            _write_state(ws, state)
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
                    resume=resume_flag,
                    interactive=False,
                    role_temperatures=role_temperatures,
                    max_parallel_tasks=max_parallel_tasks,
                    progress_callback=make_cli_progress_callback(),
                )
            )
        except ValueError as e:
            workflow_error = e
            ok = False

        attempt_record["ended_at"] = _utc_now()
        attempt_record["ok"] = ok
        if workflow_error is not None:
            attempt_record["reason"] = str(workflow_error)
        attempts = state.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
            state["attempts"] = attempts
        attempts.append(attempt_record)
        _write_state(ws, state)

        if ok:
            state["status"] = "completed"
            state["phase"] = "completed"
            _write_state(ws, state)
            break

        if attempt_index >= policy.max_iterations:
            state["status"] = "failed"
            state["phase"] = "failed"
            state["failure_reason"] = "max_iterations_exceeded"
            _write_state(ws, state)
            break

        state["phase"] = "auto_fix_retry"
        _write_state(ws, state)

    llm_usage = client.usage_summary()
    run_metadata["llm_usage"] = llm_usage
    run_metadata["result_ok"] = bool(ok)
    run_metadata["run_completed_at"] = _utc_now()
    ws.write_text(".autodev/run_metadata.json", json_dumps(run_metadata))

    report_json, report_md = _render_report(state, ok=ok, last_validation=last_validation)
    ws.write_text(AUTONOMOUS_REPORT_JSON, json_dumps(report_json))
    ws.write_text(AUTONOMOUS_REPORT_MD, report_md)

    write_report(ws.root, prd_struct, plan, last_validation, ok)
    _log_event(
        "autonomous.run_complete",
        run_id=run_id,
        request_id=request_id,
        profile=profile_name,
        ok=ok,
        output_dir=os.path.abspath(run_out),
        iterations=state.get("current_iteration"),
        max_iterations=policy.max_iterations,
        llm_usage=llm_usage,
    )
    print(
        {
            "ok": ok,
            "out": os.path.abspath(run_out),
            "iterations": state.get("current_iteration"),
            "max_iterations": policy.max_iterations,
            "llm_usage": llm_usage,
        }
    )
    if not ok:
        raise SystemExit(1)


def _status(argv: list[str]) -> None:
    parser = _build_cli_parser()
    args = parser.parse_args(["status", *argv])
    run_dir = str(Path(args.run_dir).expanduser().resolve())
    ws = Workspace(run_dir)
    state = _load_state(ws)
    if state is None:
        raise SystemExit(f"autonomous state not found: {run_dir}/{AUTONOMOUS_STATE_FILE}")
    print(json_dumps(state))


def cli(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("Usage: autodev autonomous <start|status> ...")
    action = argv[0]
    if action == "start":
        _start(argv[1:])
        return
    if action == "status":
        _status(argv[1:])
        return
    raise SystemExit(f"Unknown autonomous subcommand: {action}")
