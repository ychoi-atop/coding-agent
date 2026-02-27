from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Set, Tuple, cast
from uuid import uuid4

from jsonschema import validate  # type: ignore[import-untyped]

from .llm_client import LLMClient
from .json_utils import strict_json_loads, json_dumps
from .roles import prompts
from .schemas import PRD_SCHEMA, PLAN_SCHEMA, CHANGESET_SCHEMA, ARCHITECTURE_SCHEMA, REVIEW_SCHEMA, PRD_ANALYSIS_SCHEMA, OPENAPI_SPEC_SCHEMA, ACCEPTANCE_TEST_SCHEMA, DB_SCHEMA_SCHEMA
from .workspace import Workspace, Change
from .exec_kernel import ExecKernel
from .env_manager import EnvManager
from .validators import Validators
from .context_engine import CodeIndex, ContextSelector
from .tools import ToolExecutor
from .failure_analyzer import (
    analyze_failures,
    build_escalated_guidance,
    build_persistent_error_warnings,
    deduplicate_for_guidance,
    determine_escalation_level,
    fingerprint_failures,
    RepairHistory,
)
from .progress import ProgressEmitter
from .run_trace import RunTrace, EventType

# ---------------------------------------------------------------------------
# Re-exports from sub-modules for backward compatibility.
# All existing ``from autodev.loop import <symbol>`` continues to work.
# ---------------------------------------------------------------------------
from .loop_utils import (  # noqa: F401
    _log_event,
    _safe_short_text,
    _shorten_text,
    _hash_payload,
    _write_json,
    _ordered_unique,
    _msg,
    DEFAULT_TASK_SOFT_VALIDATORS,
    DEFAULT_VALIDATOR_FALLBACK,
    QUALITY_SUMMARY_FILE,
    QUALITY_TASK_FILE_TMPL,
    QUALITY_TASK_LAST_FILE_TMPL,
    QUALITY_PROFILE_FILE,
    QUALITY_SUMMARY_METADATA_FILE,
    QUALITY_RESOLUTION_FILE,
    REPAIR_HISTORY_FILE,
    PLAN_CACHE_FILE,
    CHECKPOINT_FILE,
    PLAN_CACHE_VERSION,
    FULL_REPO_VALIDATORS,
    HANDOFF_REQUIRED_FIELDS,
    DEFAULT_MAX_PARALLEL_TASKS,
    RECOMMENDED_MAX_PARALLEL_TASKS,
    CONSECUTIVE_FAILURE_FAIL_FAST_THRESHOLD,
    RUN_TRACE_FILE,
)
from .loop_validators import (  # noqa: F401
    _dynamic_concurrency,
    _resolve_gate_profile,
    _resolve_validators,
    _failure_signature,
    _failed_validator_names,
    _merge_validation_rows,
    _validations_ok,
    _resolve_soft_fail,
    _resolve_repeat_failure_guard,
    _build_validator_counts,
    _build_pass_map,
    _build_quality_row,
    _build_task_summary_rows,
    _summarize_run,
    _quality_metadata_from_changeset,
    _extract_fingerprint_digests,
)
from .context_cache import IncrementalContextCache
from .perf_baseline import record_and_check as _perf_baseline_check
from .task_scheduler import (
    TaskTimingStore,
    collect_task_timings,
    schedule_batch_chunks,
    schedule_level_tasks,
)
from .loop_checkpoint import (  # noqa: F401
    _generation_cache_key,
    _read_generation_cache,
    _write_generation_cache,
    _read_checkpoint,
    _write_checkpoint,
)
from .loop_tasks import (  # noqa: F401
    _toposort,
    _toposort_levels,
    _task_file_set,
    _partition_level_for_parallel,
    _is_glob_pattern,
    _match_task_file_pattern,
    _canonicalize_task_files,
    _build_files_context,
    _detect_incremental_mode,
    _write_change_summary,
)
from .loop_payloads import (  # noqa: F401
    _build_task_payload,
    _coerce_prd_payload,
    _coerce_plan_payload,
    _validate_handoff_fields,
    _extract_performance_hints,
    _is_perf_gate_failure,
    _perf_failure_rows,
    _perf_repair_task_files,
    _targeted_perf_validator_set,
)

logger = logging.getLogger("autodev")


async def _llm_json(
    client: LLMClient,
    system: str,
    user: str,
    schema: Dict[str, Any],
    max_repair: int = 2,
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    profile: str | None = None,
    component: str = "llm",
    post_process: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None,
    semantic_validator: Callable[[Dict[str, Any]], str | None] | None = None,
    temperature: float = 0.2,
    role_hint: str | None = None,
) -> Dict[str, Any]:
    run_id = run_id or uuid4().hex
    request_id = request_id or uuid4().hex
    prompt_user = user
    last_raw = ""
    last_error: Exception | None = None

    for attempt in range(max_repair + 1):
        retry_count = attempt + 1
        _log_event(
            "llm.parse_attempt",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component=component,
            attempt=retry_count,
            max_attempts=max_repair + 1,
            safe_payload=_safe_short_text(prompt_user, limit=220),
            temperature=temperature,
        )

        try:
            raw = await client.chat(_msg(system, prompt_user), temperature=temperature, role_hint=role_hint)
            last_raw = raw
        except Exception as e:
            raise ValueError(
                "LLM call failed while generating structured output "
                f"(attempt {retry_count}/{max_repair + 1}): {e}"
            ) from e

        try:
            data = strict_json_loads(raw)
            parsed = data
            if post_process:
                try:
                    parsed = post_process(data)
                    validate(instance=parsed, schema=schema)
                    semantic_error = semantic_validator(parsed) if semantic_validator else None
                    if semantic_error:
                        raise ValueError(semantic_error)
                    _log_event(
                        "llm.parse_success",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        component=f"{component}._post_process",
                        attempts=retry_count,
                        repaired_fields=list(parsed.keys())[:20],
                    )
                    return parsed
                except Exception:
                    pass
            validate(instance=parsed, schema=schema)
            semantic_error = semantic_validator(parsed) if semantic_validator else None
            if semantic_error:
                raise ValueError(semantic_error)
            _log_event(
                "llm.parse_success",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component=component,
                attempts=retry_count,
            )
            return parsed
        except Exception as e:
            last_error = e
            if attempt >= max_repair:
                break
            error_text = str(e)
            if error_text.startswith("MISSING_HANDOFF_FIELDS:"):
                missing = [x.strip() for x in error_text.split(":", 1)[1].split(",") if x.strip()]
                repair_user = f"""Your previous JSON is missing required handoff fields: {', '.join(missing)}.

Return ONLY corrected JSON.
Keep existing intent and code changes, and fill all required handoff fields.
Do not include markdown fences or extra text.
"""
                _log_event(
                    "handoff.repair_requested",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    component=component,
                    attempt=retry_count,
                    missing_fields=missing,
                )
            else:
                repair_user = f"""Your previous output did not match the required JSON schema.
Error: {e}

Return ONLY a corrected JSON object that matches the schema.
Do not include markdown fences or additional text.
"""
                _log_event(
                    "llm.parse_retry_requested",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    component=component,
                    attempt=retry_count,
                    raw_preview=_safe_short_text(last_raw),
                    error=error_text,
                )
            prompt_user = repair_user

    if last_error is None:
        raise ValueError("LLM output could not be validated, but no parser/runtime error was captured.")

    last_error_text = str(last_error)
    if last_error_text.startswith("MISSING_HANDOFF_FIELDS:"):
        missing = [x.strip() for x in last_error_text.split(":", 1)[1].split(",") if x.strip()]
        _log_event(
            "handoff.incomplete",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component=component,
            missing_fields=missing,
            attempts=max_repair + 1,
            status="re_request_exhausted",
        )

    raise ValueError(
        "Structured JSON generation failed after "
        f"{max_repair + 1} attempts. Last error: {last_error}. "
        f"Last raw output: { _shorten_text(last_raw) }"
    )


async def run_autodev_enterprise(
    client: LLMClient,
    ws: Workspace,
    prd_markdown: str,
    template_root: str,
    template_candidates: List[str],
    validators_enabled: List[str],
    audit_required: bool,
    max_fix_loops_total: int,
    max_fix_loops_per_task: int,
    max_json_repair: int,
    task_soft_validators: List[str] | None = None,
    final_soft_validators: List[str] | None = None,
    quality_profile: Dict[str, Any] | None = None,
    disable_docker_build: bool = False,
    verbose: bool = True,
    resume: bool = False,
    interactive: bool = False,
    role_temperatures: Dict[str, float] | None = None,
    max_parallel_tasks: int = DEFAULT_MAX_PARALLEL_TASKS,
    enable_snapshots: bool = True,
    continue_on_failure: bool = True,
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    profile: str | None = None,
    progress_callback: Callable[[Dict[str, Any]], None] | None = None,
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], Any]:
    run_id = run_id or uuid4().hex
    request_id = request_id or uuid4().hex

    if max_parallel_tasks <= 0:
        max_parallel_tasks = 1

    _log_event(
        "run_enterprise.start",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        event_scope="run",
        validators_enabled=validators_enabled,
        disable_docker_build=disable_docker_build,
        template_candidates=template_candidates,
        max_fix_loops_total=max_fix_loops_total,
        max_fix_loops_per_task=max_fix_loops_per_task,
        max_json_repair=max_json_repair,
        max_parallel_tasks=max_parallel_tasks,
        max_parallel_tasks_recommended=RECOMMENDED_MAX_PARALLEL_TASKS,
        resume=resume,
        interactive=interactive,
        continue_on_failure=continue_on_failure,
    )

    # -- RunTrace initialisation ------------------------------------------------
    trace = RunTrace(run_id=run_id, request_id=request_id, profile=profile)
    trace.record(
        EventType.RUN_START,
        validators_enabled=validators_enabled,
        max_parallel_tasks=max_parallel_tasks,
    )

    # -- Progress emitter -------------------------------------------------------
    progress = ProgressEmitter(callback=progress_callback)
    progress.run_start(run_id)

    p = prompts()
    role_temperatures = role_temperatures or {}
    if max_parallel_tasks > RECOMMENDED_MAX_PARALLEL_TASKS:
        _log_event(
            "task.concurrency_high",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            configured=max_parallel_tasks,
            recommended_max=RECOMMENDED_MAX_PARALLEL_TASKS,
        )

    def _role_temperature(component: str, default: float = 0.2) -> float:
        value = role_temperatures.get(component)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if component == "perf_fixer":
            fixer_value = role_temperatures.get("fixer")
            if isinstance(fixer_value, (int, float)) and not isinstance(fixer_value, bool):
                return float(fixer_value)
        return default

    quality_profile = quality_profile or {}
    cache_key = _generation_cache_key(
        prd_markdown=prd_markdown,
        template_candidates=template_candidates,
        validators_enabled=validators_enabled,
        quality_profile=quality_profile,
    )
    cache_payload = _read_generation_cache(ws)
    use_cached = (
        isinstance(cache_payload, dict)
        and cache_payload.get("cache_key") == cache_key
        and isinstance(cache_payload.get("prd_struct"), dict)
        and isinstance(cache_payload.get("plan"), dict)
    )

    architecture: Dict[str, Any] | None = None
    prd_analysis: Dict[str, Any] | None = None

    if use_cached:
        cached = cast(Dict[str, Any], cache_payload)
        prd_struct = cast(Dict[str, Any], cached["prd_struct"])
        plan = cast(Dict[str, Any], cached["plan"])
        # Restore cached architecture and prd_analysis if present.
        architecture = cached.get("architecture")
        prd_analysis = cached.get("prd_analysis")
        _log_event(
            "run.cache_hit",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            cache_key=cache_key,
        )
        # Mark generation phases as complete (cached)
        for _cached_phase in ("prd_analysis", "architecture", "planning"):
            progress.phase_start(_cached_phase)
            progress.phase_end(_cached_phase)
    else:
        # 0) Analyze PRD quality (optional — skipped if role not defined)
        trace.start_phase("prd_analysis")
        progress.phase_start("prd_analysis")
        if "prd_analyst" in p:
            prd_analysis = await _llm_json(
                client,
                p["prd_analyst"]["system"],
                f"PRD_MARKDOWN:\n{prd_markdown}\n\nTASK:\n{p['prd_analyst']['task']}",
                PRD_ANALYSIS_SCHEMA,
                max_repair=max_json_repair,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component="prd_analyst",
                temperature=_role_temperature("prd_analyst"),
                role_hint="prd_analyst",
            )
            _write_json(ws, ".autodev/prd_analysis.json", prd_analysis)
            _log_event(
                "run.prd_analysis_complete",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                completeness_score=prd_analysis.get("completeness_score"),
                ambiguities=len(prd_analysis.get("ambiguities", [])),
                missing=len(prd_analysis.get("missing_requirements", [])),
                contradictions=len(prd_analysis.get("contradictions", [])),
                risks=len(prd_analysis.get("risks", [])),
            )

            # Interactive mode: pause if PRD has significant issues
            if interactive and prd_analysis.get("completeness_score", 100) < 70:
                questions = prd_analysis.get("clarification_questions", [])
                print(f"[interactive] PRD completeness: {prd_analysis.get('completeness_score')}/100")
                if questions:
                    print("[interactive] Clarification questions:")
                    for i, q in enumerate(questions, 1):
                        print(f"  {i}. {q}")
                decision = input("Continue despite PRD quality issues? [Y/n] ").strip().lower()
                if decision in {"n", "no"}:
                    _log_event(
                        "run.prd_analysis_aborted",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        completeness_score=prd_analysis.get("completeness_score"),
                    )
                    progress.run_end(run_id, ok=False)
                    return False, {}, {}, []

        # 1) Normalize PRD with LLM (strict schema)
        prd_norm_input = f"PRD_MARKDOWN:\n{prd_markdown}\n\nTASK:\n{p['prd_normalizer']['task']}"
        if prd_analysis is not None:
            prd_norm_input += f"\n\nPRD_ANALYSIS (detected issues — use to improve normalization):\n{json_dumps(prd_analysis)}"
        prd_struct = await _llm_json(
            client,
            p["prd_normalizer"]["system"],
            prd_norm_input,
            PRD_SCHEMA,
            max_repair=max_json_repair,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component="prd_normalizer",
            post_process=_coerce_prd_payload,
            temperature=_role_temperature("prd_normalizer"),
            role_hint="prd_normalizer",
        )

        trace.end_phase("prd_analysis")
        progress.phase_end("prd_analysis")

        # 2) Architecture design (optional — skipped if role not defined)
        trace.start_phase("architecture")
        progress.phase_start("architecture")
        if "architect" in p:
            architecture = await _llm_json(
                client,
                p["architect"]["system"],
                json_dumps({
                    "prd_struct": prd_struct,
                    "task": p["architect"]["task"],
                }),
                ARCHITECTURE_SCHEMA,
                max_repair=max_json_repair,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component="architect",
                temperature=_role_temperature("architect", 0.3),
                role_hint="architect",
            )
            _write_json(ws, ".autodev/architecture.json", architecture)
            _log_event(
                "run.architecture_generated",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                components=len(architecture.get("components", [])),
                data_models=len(architecture.get("data_models", [])),
                api_contracts=len(architecture.get("api_contracts", [])),
            )

        # 2.5) Generate OpenAPI spec from architecture (optional)
        _api_spec: Dict[str, Any] | None = None
        if "api_spec_generator" in p and architecture is not None:
            _api_contracts = architecture.get("api_contracts", [])
            if _api_contracts:
                _spec_input: Dict[str, Any] = {
                    "task": p["api_spec_generator"]["task"],
                    "api_contracts": _api_contracts,
                    "data_models": architecture.get("data_models", []),
                    "project_name": prd_struct.get("title", "API"),
                }
                _api_spec = await _llm_json(
                    client,
                    p["api_spec_generator"]["system"],
                    json_dumps(_spec_input),
                    OPENAPI_SPEC_SCHEMA,
                    max_repair=max_json_repair,
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    component="api_spec_generator",
                    temperature=_role_temperature("api_spec_generator", 0.15),
                    role_hint="api_spec_generator",
                )
                _spec_yaml = _api_spec.get("spec_yaml", "")
                if _spec_yaml:
                    ws.write_text("openapi.yaml", _spec_yaml)
                _write_json(ws, ".autodev/api_spec.json", _api_spec)
                _log_event(
                    "run.api_spec_generated",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    paths=len(_api_spec.get("paths", [])),
                    components=len(_api_spec.get("components_schemas", [])),
                )

        # 2.55) Generate DB schema from architecture data_models (optional)
        _db_schema: Dict[str, Any] | None = None
        if "db_schema_generator" in p and architecture is not None:
            _db_models = architecture.get("data_models", [])
            if _db_models:
                _db_input: Dict[str, Any] = {
                    "task": p["db_schema_generator"]["task"],
                    "data_models": _db_models,
                    "database": architecture.get("database", {}),
                    "project_name": prd_struct.get("title", ""),
                }
                _db_schema = await _llm_json(
                    client,
                    p["db_schema_generator"]["system"],
                    json_dumps(_db_input),
                    DB_SCHEMA_SCHEMA,
                    max_repair=max_json_repair,
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    component="db_schema_generator",
                    temperature=_role_temperature("db_schema_generator", 0.1),
                    role_hint="db_schema_generator",
                )
                _db_source = _db_schema.get("source_code", "")
                if _db_source:
                    ws.write_text("src/app/db/models.py", _db_source)
                _db_migration = _db_schema.get("alembic_migration", "")
                if _db_migration:
                    ws.write_text("src/app/db/migrations/001_initial.py", _db_migration)
                _write_json(ws, ".autodev/db_schema.json", _db_schema)
                _log_event(
                    "run.db_schema_generated",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    models=len(_db_schema.get("models", [])),
                    relationships=len(_db_schema.get("relationships", [])),
                )

        # 2.5) Build code index for existing codebase awareness (pre-scaffold)
        _pre_code_index = CodeIndex(ws)
        _pre_code_index.scan()
        _pre_context_selector = ContextSelector(_pre_code_index, ws)

        # 2.6) Check if incremental mode should influence planner
        _pre_incremental = _detect_incremental_mode(_pre_code_index)

        trace.end_phase("architecture")
        progress.phase_end("architecture")

        # 3) Plan
        trace.start_phase("planning")
        progress.phase_start("planning")
        planner_input: Dict[str, Any] = {
            "template_candidates": template_candidates,
            "prd_struct": prd_struct,
            "task": p["planner"]["task"],
        }
        if architecture is not None:
            planner_input["architecture"] = architecture
        if _api_spec is not None:
            planner_input["api_spec_summary"] = {
                "paths": [{"path": p_item.get("path"), "method": p_item.get("method")} for p_item in _api_spec.get("paths", [])],
                "has_openapi_yaml": True,
            }
        # Inject existing codebase context for planner awareness
        if _pre_code_index.files:
            planner_input["existing_codebase"] = _pre_context_selector.select_for_planner(
                prd_keywords=[prd_struct.get("title", "")] + prd_struct.get("goals", []),
            )
        if _pre_incremental:
            planner_input["incremental_mode"] = True
            planner_input["existing_file_count"] = len(_pre_code_index.files)
        if _db_schema is not None:
            planner_input["db_schema_summary"] = {
                "models": [m["name"] for m in _db_schema.get("models", [])],
                "has_db_models": True,
            }

        # Dynamically enhance planner prompt for incremental mode
        _planner_system = p["planner"]["system"]
        if _pre_incremental:
            from .roles import INCREMENTAL_PLANNER_ADDENDUM
            _planner_system += "\n" + INCREMENTAL_PLANNER_ADDENDUM

        plan = await _llm_json(
            client,
            _planner_system,
            json_dumps(planner_input),
            PLAN_SCHEMA,
            max_repair=max_json_repair,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component="planner",
            post_process=lambda payload: _coerce_plan_payload(payload, template_candidates),
            temperature=_role_temperature("planner"),
            role_hint="planner",
        )
        _write_generation_cache(ws, cache_key, prd_struct, plan, architecture, prd_analysis)

    # 2.7) Detect incremental mode (works for both cache-hit and fresh plan paths)
    _incr_code_index = CodeIndex(ws)
    _incr_code_index.scan()
    incremental_mode = _detect_incremental_mode(_incr_code_index)
    _pre_existing_files: set[str] = set(ws.list_context_files(max_files=None)) if incremental_mode else set()
    if incremental_mode:
        _log_event(
            "run.incremental_mode_detected",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            indexed_files=len(_incr_code_index.files),
            total_symbols=sum(len(m.symbols) for m in _incr_code_index.files.values()),
            pre_existing_file_count=len(_pre_existing_files),
        )

    # 3) Scaffold
    project_type = plan["project"]["type"]
    template_dir = os.path.join(template_root, project_type)
    ws.apply_template(template_dir)
    _log_event(
        "run.scaffolded",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        project_type=project_type,
        template_dir=template_dir,
    )

    repo_files_for_plan = ws.list_context_files(max_files=None)
    try:
        plan = _canonicalize_task_files(plan, repo_files_for_plan)
    except ValueError as e:
        _log_event(
            "task_file_resolution.rejected",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            project_type=project_type,
            error=str(e),
            candidate_count=len(repo_files_for_plan),
            unresolved_globs=_safe_short_text(json_dumps(plan), limit=700),
        )
        repair_payload: Dict[str, Any] = {
            "task": "Repair ONLY tasks[].files in the PLAN so each glob matches existing repo files.",
            "constraints": [
                f"Keep project.type unchanged: {project_type}",
                "Keep task intent and ordering unless required to fix file targeting.",
                "Use concrete file paths where possible.",
                "If glob patterns are used, each must match at least one file in repo_files.",
            ],
            "error": str(e),
            "repo_files": repo_files_for_plan[:1500],
            "current_plan": plan,
        }
        repaired_plan = await _llm_json(
            client,
            _planner_system,
            json_dumps(repair_payload),
            PLAN_SCHEMA,
            max_repair=max_json_repair,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component="planner_repair",
            post_process=lambda payload: _coerce_plan_payload(payload, template_candidates),
            temperature=_role_temperature("planner_repair", _role_temperature("planner")),
            role_hint="planner",
        )
        if repaired_plan["project"]["type"] != project_type:
            raise ValueError("Planner repair changed project.type; refusing to continue.")
        plan = _canonicalize_task_files(repaired_plan, repo_files_for_plan)
        _log_event(
            "task_file_resolution.repaired",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            project_type=project_type,
        )

    _write_json(ws, ".autodev/prd_struct.json", prd_struct)
    _write_json(ws, ".autodev/plan.json", plan)

    # 3.1) Build code index for context-aware operations (post-scaffold)
    code_index = CodeIndex(ws)
    code_index.scan()
    context_selector = ContextSelector(code_index, ws)

    # 3.5) Generate acceptance test skeletons (optional — skipped if role not defined)
    if "acceptance_test_generator" in p:
        for _atg_task in plan.get("tasks", []):
            _atg_acceptance = _atg_task.get("acceptance", [])
            _atg_quality = _atg_task.get("quality_expectations", {})
            if not _atg_quality.get("requires_tests", False) and not _atg_acceptance:
                continue

            _atg_input: Dict[str, Any] = {
                "task": p["acceptance_test_generator"]["task"],
                "task_id": _atg_task["id"],
                "task_goal": _atg_task.get("goal", ""),
                "acceptance_criteria": _atg_acceptance,
                "quality_expectations": _atg_quality,
                "project_type": plan["project"].get("type", ""),
                "project_name": plan["project"].get("name", ""),
                "prd_acceptance": prd_struct.get("acceptance_criteria", []),
                "task_files": _atg_task.get("files", []),
            }
            if architecture is not None:
                _atg_input["architecture_summary"] = {
                    "components": [c["name"] for c in architecture.get("components", [])],
                    "api_contracts": architecture.get("api_contracts", []),
                }

            _atg_scaffold = await _llm_json(
                client,
                p["acceptance_test_generator"]["system"],
                json_dumps(_atg_input),
                ACCEPTANCE_TEST_SCHEMA,
                max_repair=max_json_repair,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component="acceptance_test_generator",
                temperature=_role_temperature("acceptance_test_generator", 0.15),
                role_hint="acceptance_test_generator",
            )

            _atg_test_file = _atg_scaffold.get("test_file", "")
            _atg_source = _atg_scaffold.get("source_code", "")
            if _atg_test_file and _atg_source:
                # Ensure unique test file per task to avoid file overlap between parallel tasks
                _atg_base, _atg_ext = os.path.splitext(_atg_test_file)
                _atg_unique_file = f"{_atg_base}_{_atg_task['id']}{_atg_ext}"
                ws.write_text(_atg_unique_file, _atg_source)
                _atg_scaffold["test_file"] = _atg_unique_file
                _write_json(ws, f".autodev/acceptance_tests_{_atg_task['id']}.json", _atg_scaffold)
                # Add test file to task's files if not already present
                if _atg_unique_file not in _atg_task.get("files", []):
                    _atg_task.setdefault("files", []).append(_atg_unique_file)

            _log_event(
                "run.acceptance_tests_generated",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                task_id=_atg_task["id"],
                test_file=_atg_test_file,
                test_count=len(_atg_scaffold.get("test_cases", [])),
            )

    if interactive:
        plan_path = os.path.join(ws.root, ".autodev", "plan.json")
        print(f"[interactive] Plan generated: {plan_path}")
        decision = input("Proceed with implementation? [Y/n] ").strip().lower()
        if decision in {"n", "no"}:
            _log_event(
                "run.interactive_aborted",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                plan_path=plan_path,
            )
            progress.run_end(run_id, ok=False)
            return False, prd_struct, plan, []

    trace.end_phase("planning")
    progress.phase_end("planning")

    quality_profile = _resolve_gate_profile(
        quality_profile=quality_profile,
        gate_profile=plan.get("project", {}).get("quality_gate_profile"),
    )
    quality_profile.setdefault("resolved_from", plan.get("project", {}).get("quality_gate_profile", "balanced"))
    _write_json(ws, QUALITY_PROFILE_FILE, quality_profile)
    _write_json(ws, QUALITY_SUMMARY_METADATA_FILE, _summarize_run(quality_profile, plan))

    profile_policy = quality_profile.get("validator_policy") or {}
    task_soft = _resolve_soft_fail(
        profile_policy.get("per_task", {}) if isinstance(profile_policy, dict) else profile_policy,
        task_soft_validators,
        compact_key="per_task_soft",
    )
    final_soft = _resolve_soft_fail(
        profile_policy.get("final", {}) if isinstance(profile_policy, dict) else profile_policy,
        final_soft_validators,
        compact_key="final_soft",
    )
    if not task_soft and isinstance(quality_profile, dict):
        task_soft = _resolve_soft_fail(quality_profile, task_soft_validators, compact_key="per_task_soft")
    if not final_soft and isinstance(quality_profile, dict):
        final_soft = _resolve_soft_fail(quality_profile, final_soft_validators, compact_key="final_soft")
    if task_soft_validators is None and not task_soft:
        task_soft = set(task_soft_validators or DEFAULT_TASK_SOFT_VALIDATORS)
    if final_soft_validators is None and not final_soft:
        final_soft = set(final_soft_validators or [])
    repeat_guard = _resolve_repeat_failure_guard(quality_profile)
    repeat_guard_enabled = bool(repeat_guard["enabled"])
    repeat_guard_max_retries = int(repeat_guard["max_retries_before_targeted_fix"])

    _log_event(
        "run.quality_profile_resolved",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        resolved_from=quality_profile.get("resolved_from"),
        task_soft_fail=sorted(task_soft),
        final_soft_fail=sorted(final_soft),
        repeat_failure_guard_enabled=repeat_guard_enabled,
        repeat_failure_guard_retries=repeat_guard_max_retries,
    )

    effective_validators_enabled = list(dict.fromkeys(validators_enabled))
    if disable_docker_build and "docker_build" in effective_validators_enabled:
        effective_validators_enabled = [
            v for v in effective_validators_enabled if v != "docker_build"
        ]

    # 4) Prepare env
    kernel = ExecKernel(cwd=ws.root, timeout_sec=1800)
    env = EnvManager(kernel)
    system_python = os.environ.get("AUTODEV_SYSTEM_PYTHON", "python3")
    env.ensure_venv(system_python=system_python)
    include_dev = task_soft_validators is not None or ws.exists("requirements-dev.txt")
    env.install_requirements(include_dev=include_dev)
    validators = Validators(kernel, env)
    tool_executor = ToolExecutor(kernel, env, ws)

    tasks = _toposort(plan["tasks"])
    checkpoint_payload = _read_checkpoint(ws) if resume else None
    completed_task_ids: Set[str] = set()
    if isinstance(checkpoint_payload, dict):
        completed_raw = checkpoint_payload.get("completed_task_ids")
        if isinstance(completed_raw, list):
            completed_task_ids = {str(task_id) for task_id in completed_raw if str(task_id).strip()}
    _write_checkpoint(
        ws,
        sorted(completed_task_ids),
        status="running",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
    )
    if resume:
        _log_event(
            "run.checkpoint_loaded",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            checkpoint_found=checkpoint_payload is not None,
            completed_tasks=sorted(completed_task_ids),
        )

    quality_summary: Dict[str, Any] = {
        "project": plan.get("project", {}),
        "run_level_soft_fail": {
            "per_task": sorted(task_soft),
            "final": sorted(final_soft),
        },
        "repeat_failure_guard": {
            "enabled": repeat_guard_enabled,
            "max_retries_before_targeted_fix": repeat_guard_max_retries,
        },
        "validator_enabled": effective_validators_enabled,
        "resolved_quality_profile": quality_profile,
        "concurrency": {
            "max_parallel_tasks": max_parallel_tasks,
            "recommended_max_parallel_tasks": RECOMMENDED_MAX_PARALLEL_TASKS,
        },
        "tasks": [],
    }

    total_fix_loops = 0
    task_failures = 0
    last_validation = None
    unresolved: List[str] = []
    failed_task_ids: Set[str] = set()
    skipped_task_ids: Set[str] = set()
    repair_history = RepairHistory()
    performance_context = _extract_performance_hints(prd_struct=prd_struct, plan=plan)
    task_order = {task["id"]: index for index, task in enumerate(tasks, start=1)}
    task_levels = _toposort_levels(tasks)
    task_context_cache: Dict[str, Dict[str, str]] = {}

    # -- Intelligent task scheduling: load historical timings -------------------
    _timing_store: TaskTimingStore | None = None
    try:
        _baseline_path = os.path.join(ws.root, ".autodev", "perf_baseline.json")
        if os.path.exists(_baseline_path):
            import json as _json

            with open(_baseline_path, "r", encoding="utf-8") as _f:
                _baseline_data = _json.load(_f)
            _timing_store = TaskTimingStore.from_baseline(_baseline_data)
            if _timing_store.task_count > 0:
                _log_event(
                    "task_scheduler.loaded",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    timing_count=_timing_store.task_count,
                )
    except Exception:
        logger.debug("task_scheduler: failed to load timing store", exc_info=True)

    # -- Incremental context cache: stub unchanged files in repair loops ------
    _icc_cfg = quality_profile.get("incremental_context_cache") if isinstance(quality_profile, dict) else None
    _icc_enabled = True
    _icc_stub_fmt = "structural"
    if isinstance(_icc_cfg, dict):
        if _icc_cfg.get("enabled") is False:
            _icc_enabled = False
        if _icc_cfg.get("stub_format") in ("structural", "hash_only"):
            _icc_stub_fmt = _icc_cfg["stub_format"]
    _incremental_cache = IncrementalContextCache(
        code_index=code_index if code_index.files else None,
        enabled=_icc_enabled,
        stub_format=_icc_stub_fmt,
    )

    def _cached_files_context(task_id: str, files: List[str], goal: str = "") -> Dict[str, str]:
        cache_key = f"{task_id}:{'|'.join(files)}"
        cached = task_context_cache.get(cache_key)
        if cached is not None:
            return cached
        # Use context-aware selection when code index is available
        if code_index.files:
            ctx = context_selector.select_for_task(goal=goal, seed_files=files)
        else:
            ctx = _build_files_context(ws, files)
        # Incremental context transform: stub unchanged files
        ctx, _icc_savings = _incremental_cache.record_and_transform(task_id, ctx)
        if _icc_savings.chars_saved > 0:
            _log_event(
                "context_cache.hit",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                task_id=task_id,
                files_unchanged=_icc_savings.files_unchanged,
                chars_saved=_icc_savings.chars_saved,
                savings_pct=round(_icc_savings.savings_pct, 1),
            )
        task_context_cache[cache_key] = ctx
        return ctx

    async def _run_task_execution(
        task: Dict[str, Any],
        *,
        iteration: int,
        run_set: List[str],
    ) -> Dict[str, Any]:
        nonlocal total_fix_loops

        if verbose:
            print(f"\n== TASK {task['id']} == {task['title']}")

        progress.task_start(task["id"], task["title"])
        _log_event(
            "task.start",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            iteration=iteration,
            task_id=task["id"],
            task_title=task["title"],
            validator_focus=run_set,
            file_count=len(task["files"]),
        )

        # Snapshot workspace before task changes
        snapshot_name = f"pre_task_{task['id']}"
        if enable_snapshots:
            ws.snapshot(snapshot_name)
            progress.emit("snapshot.created", task_id=task["id"], snapshot_name=snapshot_name)
            trace.record(EventType.SNAPSHOT_CREATED, task_id=task["id"], snapshot_name=snapshot_name)

        files_ctx = _cached_files_context(task["id"], task["files"], goal=task.get("goal", ""))
        attempt_records: List[Dict[str, Any]] = []
        loops = 0
        last_failure_signature: tuple[Any, ...] = tuple()
        repeat_failure_count = 0
        consecutive_failures = 0
        repair_used = False
        previous_validation_rows: List[Dict[str, Any]] = []
        failure_analyses: List[Any] = []
        fingerprinted: List[Any] = []
        fingerprint_iteration_counts: Dict[str, int] = {}
        escalation_level = 0

        impl_payload = _build_task_payload(plan, task, performance_context=performance_context)
        impl_payload["files_context"] = files_ctx
        impl_payload["guidance"] = p["implementer"]["task"]
        # Inject pre-generated acceptance test context if available
        _atg_meta_path = f".autodev/acceptance_tests_{task['id']}.json"
        if ws.exists(_atg_meta_path):
            try:
                _atg_meta = strict_json_loads(ws.read_text(_atg_meta_path))
                _atg_file = _atg_meta.get("test_file", "")
                if _atg_file and ws.exists(_atg_file):
                    impl_payload["acceptance_tests"] = {
                        "test_file": _atg_file,
                        "source": ws.read_text(_atg_file)[:6000],
                        "hint": "Pre-generated acceptance tests exist. Implement code so these tests PASS. Do NOT delete or weaken the test assertions.",
                    }
            except Exception:
                pass
        # Inject OpenAPI spec context if available
        if ws.exists("openapi.yaml"):
            try:
                impl_payload["api_spec"] = {
                    "file": "openapi.yaml",
                    "source": ws.read_text("openapi.yaml")[:4000],
                    "hint": "Implement API endpoints matching this OpenAPI spec exactly. Respect paths, methods, request/response schemas, and status codes.",
                }
            except Exception:
                pass
        # Inject DB schema context if available
        if ws.exists("src/app/db/models.py"):
            try:
                impl_payload["db_schema"] = {
                    "file": "src/app/db/models.py",
                    "source": ws.read_text("src/app/db/models.py")[:6000],
                    "hint": "SQLAlchemy models have been pre-generated. Import and use these models. Do NOT redefine them.",
                }
            except Exception:
                pass
        # Gather tool context for implementer
        try:
            _tool_results = tool_executor.gather_context(task)
            if _tool_results:
                impl_payload["tool_context"] = ToolExecutor.serialize(_tool_results)
        except Exception:
            pass
        # Dynamically enhance implementer prompt for incremental mode
        _impl_system = p["implementer"]["system"]
        if incremental_mode:
            from .roles import INCREMENTAL_IMPLEMENTER_ADDENDUM
            _impl_system += "\n" + INCREMENTAL_IMPLEMENTER_ADDENDUM

        changeset = await _llm_json(
            client,
            _impl_system,
            json_dumps(impl_payload),
            CHANGESET_SCHEMA,
            max_repair=max_json_repair,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component="implementer",
            semantic_validator=_validate_handoff_fields,
            temperature=_role_temperature("implementer"),
            role_hint="implementer",
        )
        quality_trace = [_quality_metadata_from_changeset(changeset, task["id"], run_set)]

        changes: List[Change] = []
        for c in changeset["changes"]:
            changes.append(Change(op=c["op"], path=c["path"], content=c.get("content")))
        ws.apply_changes(changes)
        task_context_cache.pop(f"{task['id']}:{'|'.join(task['files'])}", None)

        # -- Code review (optional — skipped if role not defined) --
        if "reviewer" in p:
            review_files_ctx: Dict[str, str] = {}
            for ch in changeset["changes"]:
                if ch["op"] in ("write", "patch") and ws.exists(ch["path"]):
                    try:
                        review_files_ctx[ch["path"]] = ws.read_text(ch["path"])[:8000]
                    except Exception:
                        pass
            review_input: Dict[str, Any] = {
                "task": p["reviewer"]["task"],
                "task_goal": task.get("goal", ""),
                "acceptance_criteria": task.get("acceptance", []),
                "changeset_summary": changeset.get("summary", ""),
                "changed_files": review_files_ctx,
            }
            if architecture is not None:
                review_input["architecture"] = architecture
            review = await _llm_json(
                client,
                p["reviewer"]["system"],
                json_dumps(review_input),
                REVIEW_SCHEMA,
                max_repair=max_json_repair,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component="reviewer",
                temperature=_role_temperature("reviewer", 0.15),
                role_hint="reviewer",
            )
            _write_json(ws, f".autodev/task_{task['id']}_review.json", review)
            _log_event(
                "task.review_completed",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                task_id=task["id"],
                verdict=review.get("overall_verdict", "unknown"),
                findings_count=len(review.get("findings", [])),
                blocking_count=len(review.get("blocking_issues", [])),
            )

            # If reviewer found blocking issues, feed them as extra context to the fixer
            blocking = review.get("blocking_issues", [])
            if blocking and review.get("overall_verdict") == "request_changes":
                review_ctx = (
                    "Code review found blocking issues:\n"
                    + "\n".join(f"- {b}" for b in blocking)
                )
                impl_payload["review_feedback"] = review_ctx

        task_last_validation: List[Dict[str, Any]] = []

        while True:
            start = time.perf_counter()
            progress.validation_start(task["id"], run_set)
            if previous_validation_rows:
                failed_names = _failed_validator_names(previous_validation_rows)
                run_names = [name for name in run_set if name in failed_names]
                if not run_names:
                    run_names = list(run_set)
                if hasattr(validators, "run_one"):
                    rerun_rows = []
                    for name in run_names:
                        rerun_rows.append(
                            Validators.serialize([
                                validators.run_one(
                                    name,
                                    audit_required=audit_required,
                                    phase="per_task",
                                    run_id=run_id,
                                    request_id=request_id,
                                    profile=profile,
                                    task_id=task["id"],
                                    iteration=loops + 1,
                                )
                            ])[0]
                        )
                    validation_results = _merge_validation_rows(previous_validation_rows, rerun_rows, run_set)
                else:
                    validation_results = Validators.serialize(
                        validators.run_all(
                            run_set,
                            audit_required=audit_required,
                            soft_validators=task_soft,
                            phase="per_task",
                            run_id=run_id,
                            request_id=request_id,
                            profile=profile,
                            task_id=task["id"],
                            iteration=loops + 1,
                        )
                    )
            else:
                validation_results = Validators.serialize(
                    validators.run_all(
                        run_set,
                        audit_required=audit_required,
                        soft_validators=task_soft,
                        phase="per_task",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        task_id=task["id"],
                        iteration=loops + 1,
                    )
                )
            task_last_validation = validation_results
            previous_validation_rows = task_last_validation
            progress.validation_end(task["id"], ok=_validations_ok(task_last_validation, task_soft))
            signature = _failure_signature(task_last_validation)
            duration_ms = int((time.perf_counter() - start) * 1000)
            row = _build_quality_row(
                task_id=task["id"],
                attempt=loops + 1,
                run_set=run_set,
                validation_rows=task_last_validation,
                duration_ms=duration_ms,
                soft_validators=task_soft,
                all_ok=_validations_ok(task_last_validation, task_soft),
                quality_notes=quality_trace[-1]["quality_notes"],
                validation_links=quality_trace[-1]["validation_links"],
                repair_pass=repair_used,
            )
            attempt_records.append(row)

            failures = [
                {"name": v["name"], "status": v["status"], "error": v.get("error_classification")}
                for v in task_last_validation
                if not v["ok"]
            ]
            _log_event(
                "validation.attempt",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                iteration=iteration,
                task_id=task["id"],
                attempt=loops + 1,
                phase="per_task",
                duration_ms=duration_ms,
                validation_status=row["status"],
                failures=failures,
                validator_count=len(task_last_validation),
                hard_failures=row["hard_failures"],
                soft_failures=row["soft_failures"],
                repair_used=repair_used,
            )

            if row["status"] == "passed":
                consecutive_failures = 0
                _log_event(
                    "task.attempt_passed",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    iteration=iteration,
                    task_id=task["id"],
                    attempt=loops + 1,
                    total_task_attempts=len(attempt_records),
                )
                break

            consecutive_failures += 1
            if consecutive_failures > CONSECUTIVE_FAILURE_FAIL_FAST_THRESHOLD:
                task_entry = {
                    "task_id": task["id"],
                    "status": "failed",
                    "attempts": len(attempt_records),
                    "validator_focus": run_set,
                    "attempt_trend": _build_task_summary_rows(attempt_records),
                    "hard_failures": attempt_records[-1]["hard_failures"],
                    "soft_failures": attempt_records[-1]["soft_failures"],
                    "last_validation": attempt_records[-1]["validations"],
                    "quality_trace": quality_trace,
                    "fail_fast": True,
                }
                _log_event(
                    "task.fail_fast_triggered",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    iteration=iteration,
                    task_id=task["id"],
                    attempt=loops + 1,
                    consecutive_failures=consecutive_failures,
                    threshold=CONSECUTIVE_FAILURE_FAIL_FAST_THRESHOLD,
                    reason="consecutive_validation_failures",
                )
                _write_json(
                    ws,
                    QUALITY_TASK_FILE_TMPL.format(task_id=task["id"]),
                    {
                        "task_id": task["id"],
                        "status": "failed",
                        "attempts": attempt_records,
                        "attempts_count": len(attempt_records),
                        "validator_focus": run_set,
                        "attempt_trend": _build_task_summary_rows(attempt_records),
                        "last_validation": attempt_records[-1]["validations"],
                        "quality_trace": quality_trace,
                        "fail_fast": True,
                    },
                )
                _write_json(
                    ws,
                    QUALITY_TASK_LAST_FILE_TMPL.format(task_id=task["id"]),
                    {
                        "validator_focus": run_set,
                        "run_level": "per_task",
                        "validation": task_last_validation,
                        "record_count": len(attempt_records),
                        "fail_fast": True,
                    },
                )
                if enable_snapshots:
                    ws.rollback(snapshot_name)
                    progress.emit("snapshot.rollback", task_id=task["id"], snapshot_name=snapshot_name)
                    trace.record(EventType.SNAPSHOT_ROLLBACK, task_id=task["id"], snapshot_name=snapshot_name)
                progress.task_end(task["id"], task["title"], ok=False)
                return {
                    "task_id": task["id"],
                    "iteration": iteration,
                    "status": "failed",
                    "task_entry": task_entry,
                    "last_validation": task_last_validation,
                }

            loops += 1
            total_fix_loops += 1
            if loops > max_fix_loops_per_task or total_fix_loops > max_fix_loops_total:
                task_entry = {
                    "task_id": task["id"],
                    "status": "failed",
                    "attempts": len(attempt_records),
                    "validator_focus": run_set,
                    "attempt_trend": _build_task_summary_rows(attempt_records),
                    "hard_failures": attempt_records[-1]["hard_failures"],
                    "soft_failures": attempt_records[-1]["soft_failures"],
                    "last_validation": attempt_records[-1]["validations"],
                    "quality_trace": quality_trace,
                }
                _write_json(
                    ws,
                    QUALITY_TASK_FILE_TMPL.format(task_id=task["id"]),
                    {
                        "task_id": task["id"],
                        "status": "failed",
                        "attempts": attempt_records,
                        "attempts_count": len(attempt_records),
                        "validator_focus": run_set,
                        "attempt_trend": _build_task_summary_rows(attempt_records),
                        "last_validation": attempt_records[-1]["validations"],
                        "quality_trace": quality_trace,
                    },
                )
                _write_json(
                    ws,
                    QUALITY_TASK_LAST_FILE_TMPL.format(task_id=task["id"]),
                    {
                        "validator_focus": run_set,
                        "run_level": "per_task",
                        "validation": task_last_validation,
                        "record_count": len(attempt_records),
                    },
                )
                _log_event(
                    "task.failed",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    iteration=iteration,
                    task_id=task["id"],
                    attempts=len(attempt_records),
                    reason="validator_limit_exceeded",
                    hard_failures=attempt_records[-1]["hard_failures"],
                    soft_failures=attempt_records[-1]["soft_failures"],
                    total_fix_loops=total_fix_loops,
                )
                if enable_snapshots:
                    ws.rollback(snapshot_name)
                    progress.emit("snapshot.rollback", task_id=task["id"], snapshot_name=snapshot_name)
                    trace.record(EventType.SNAPSHOT_ROLLBACK, task_id=task["id"], snapshot_name=snapshot_name)
                progress.task_end(task["id"], task["title"], ok=False)
                return {
                    "task_id": task["id"],
                    "iteration": iteration,
                    "status": "failed",
                    "task_entry": task_entry,
                    "last_validation": task_last_validation,
                }

            files_ctx = _cached_files_context(task["id"], task["files"], goal=task.get("goal", ""))
            same_failure_signature = bool(signature) and signature == last_failure_signature
            if same_failure_signature:
                repeat_failure_count += 1
            else:
                repeat_failure_count = 0

            # Phase 3.2: Fingerprint tracking + failure analysis + escalation
            current_digests = _extract_fingerprint_digests(task_last_validation)
            new_counts: Dict[str, int] = {}
            for _fp_digest in current_digests:
                new_counts[_fp_digest] = fingerprint_iteration_counts.get(_fp_digest, 0) + 1
            fingerprint_iteration_counts = new_counts
            max_fp_strikes = max(fingerprint_iteration_counts.values()) if fingerprint_iteration_counts else 0
            has_persistent_errors = max_fp_strikes >= 3

            failure_analyses = analyze_failures(task_last_validation)
            fingerprinted = fingerprint_failures(task_last_validation)
            escalation_level = determine_escalation_level(
                repeat_failure_count, repeat_guard_max_retries, repeat_guard_enabled,
            )
            # Boost escalation when persistent fingerprints detected
            if has_persistent_errors and escalation_level < 2:
                escalation_level = min(escalation_level + 1, 2)

            # Backward compat: derive repair_mode from escalation + existing guard logic
            repair_mode = "normal"
            targeted_fix_requested = False
            if not repair_used and repeat_guard_enabled:
                if repeat_guard_max_retries == 0:
                    targeted_fix_requested = True
                elif same_failure_signature and repeat_failure_count >= repeat_guard_max_retries:
                    targeted_fix_requested = True
            if targeted_fix_requested:
                escalation_level = max(escalation_level, 1)
                repair_mode = "targeted"
                repair_used = True
            elif escalation_level >= 2:
                repair_mode = "surgical"
                repair_used = True
            elif escalation_level == 1:
                repair_mode = "targeted"
                repair_used = True

            repair_payload = _build_task_payload(plan, task, performance_context=performance_context)
            repair_payload["files_context"] = files_ctx
            repair_payload["validation"] = task_last_validation

            # Build escalated guidance with failure analysis
            base_guidance = p["fixer"]["task"]
            repair_payload["guidance"] = build_escalated_guidance(
                level=escalation_level,
                analyses=failure_analyses,
                base_guidance=base_guidance,
                validation_rows=task_last_validation,
            )

            # Deduplicated error summary for fixer
            _dedup_summary = deduplicate_for_guidance(fingerprinted)
            if _dedup_summary:
                repair_payload["guidance"] += "\n\n" + _dedup_summary

            # Persistent error warnings (3-strike)
            _persistent_warning = build_persistent_error_warnings(fingerprint_iteration_counts)
            if _persistent_warning:
                repair_payload["guidance"] += "\n\n" + _persistent_warning

            # Cross-task hints from repair history (category + fingerprint level)
            cross_hints: List[str] = []
            for fa in failure_analyses:
                cross_hints.extend(repair_history.get_hints_for_category(fa.category))
            for ff in fingerprinted:
                for _fp in ff.fingerprints:
                    cross_hints.extend(repair_history.get_hints_for_fingerprint(_fp.digest))
            # Deduplicate hints
            _seen_hints: set[str] = set()
            _unique_hints: List[str] = []
            for _h in cross_hints:
                if _h not in _seen_hints:
                    _seen_hints.add(_h)
                    _unique_hints.append(_h)
            if _unique_hints:
                repair_payload["guidance"] += (
                    "\n\nCross-task hints:\n" + "\n".join(f"- {h}" for h in _unique_hints[:5])
                )

            last_failure_signature = signature
            # Gather tool context for fixer
            try:
                _fixer_tool_results = tool_executor.gather_context(task)
                if _fixer_tool_results:
                    repair_payload["tool_context"] = ToolExecutor.serialize(_fixer_tool_results)
            except Exception:
                pass

            progress.repair_start(task["id"], attempt=loops + 1)
            _log_event(
                "task.repair_requested",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                iteration=iteration,
                task_id=task["id"],
                attempt=loops + 1,
                repair_mode=repair_mode,
                escalation_level=escalation_level,
                failure_categories=[fa.category.value for fa in failure_analyses],
                failure_signature=str(signature)[:200],
                repeat_failure_count=repeat_failure_count,
                repeat_failure_guard_enabled=repeat_guard_enabled,
                repeat_failure_guard_retries=repeat_guard_max_retries,
            )

            # Dynamically enhance fixer prompt for incremental mode
            _fixer_system = p["fixer"]["system"]
            if incremental_mode:
                from .roles import INCREMENTAL_FIXER_ADDENDUM
                _fixer_system += "\n" + INCREMENTAL_FIXER_ADDENDUM

            fix = await _llm_json(
                client,
                _fixer_system,
                json_dumps(repair_payload),
                CHANGESET_SCHEMA,
                max_repair=max_json_repair,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                component="fixer",
                semantic_validator=_validate_handoff_fields,
                temperature=_role_temperature("fixer"),
                role_hint="fixer",
            )
            quality_trace.append(_quality_metadata_from_changeset(fix, task["id"], run_set))
            fix_changes: List[Change] = []
            for c in fix["changes"]:
                fix_changes.append(Change(op=c["op"], path=c["path"], content=c.get("content")))
            ws.apply_changes(fix_changes)
            task_context_cache.pop(f"{task['id']}:{'|'.join(task['files'])}", None)

        # Record repair outcomes for cross-task learning (with fingerprints)
        _task_resolved = attempt_records[-1]["status"] == "passed" if attempt_records else False
        if fingerprinted:
            for ff in fingerprinted:
                repair_history.record(
                    task_id=task["id"],
                    category=ff.analysis.category,
                    level=escalation_level,
                    resolved=_task_resolved,
                    fingerprints=[fp.digest for fp in ff.fingerprints],
                )
        else:
            for fa in failure_analyses:
                repair_history.record(
                    task_id=task["id"],
                    category=fa.category,
                    level=escalation_level,
                    resolved=_task_resolved,
                )

        task_entry = {
            "task_id": task["id"],
            "status": "passed",
            "attempts": len(attempt_records),
            "validator_focus": run_set,
            "attempt_trend": _build_task_summary_rows(attempt_records),
            "hard_failures": attempt_records[-1]["hard_failures"],
            "soft_failures": attempt_records[-1]["soft_failures"],
            "last_validation": attempt_records[-1]["validations"],
            "repair_passes": 1 if repair_used else 0,
            "quality_trace": quality_trace,
        }
        _write_json(
            ws,
            QUALITY_TASK_FILE_TMPL.format(task_id=task["id"]),
            {
                "task_id": task["id"],
                "status": "passed",
                "attempts": attempt_records,
                "attempts_count": len(attempt_records),
                "validator_focus": run_set,
                "attempt_trend": task_entry["attempt_trend"],
                "last_validation": attempt_records[-1]["validations"],
                "repair_passes": task_entry["repair_passes"],
                "quality_trace": quality_trace,
            },
        )
        _write_json(
            ws,
            QUALITY_TASK_LAST_FILE_TMPL.format(task_id=task["id"]),
            {
                "validator_focus": run_set,
                "run_level": "per_task",
                "validation": attempt_records[-1]["validations"],
                "record_count": len(attempt_records),
            },
        )
        # Clean up snapshot on success — no longer needed
        if enable_snapshots:
            _snap_dir = os.path.join(ws.root, ws.SNAPSHOT_DIR, snapshot_name)
            if os.path.isdir(_snap_dir):
                shutil.rmtree(_snap_dir)
        progress.task_end(task["id"], task["title"], ok=True)
        return {
            "task_id": task["id"],
            "iteration": iteration,
            "status": "passed",
            "task_entry": task_entry,
            "last_validation": attempt_records[-1]["validations"],
        }

    trace.start_phase("implementation")
    progress.set_total_tasks(len(tasks))
    progress.phase_start("implementation")

    for level_idx, level_tasks in enumerate(task_levels, start=1):
        # LPT scheduling: sort tasks by estimated duration (longest first)
        level_tasks = schedule_level_tasks(level_tasks, _timing_store)
        level_batches = _partition_level_for_parallel(level_tasks)

        # Dynamic concurrency: adjust based on remaining token budget
        try:
            _usage = client.usage_summary()
        except (AttributeError, Exception):
            _usage = {}
        effective_parallel = _dynamic_concurrency(
            max_parallel_tasks,
            _usage,
            sum(len(lev) for lev in task_levels[level_idx:]),
        )
        if effective_parallel != max_parallel_tasks:
            trace.record(
                EventType.CONCURRENCY_ADJUSTED,
                level=level_idx,
                old=max_parallel_tasks,
                new=effective_parallel,
                remaining_tokens=_usage.get("remaining_tokens"),
            )
        _log_event(
            "task.level_scheduled",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            level=level_idx,
            level_task_ids=[task["id"] for task in level_tasks],
            batch_count=len(level_batches),
            max_batch_size=max(len(batch) for batch in level_batches) if level_batches else 0,
        )

        for batch_idx, batch_tasks in enumerate(level_batches, start=1):
            runnable: List[tuple[Dict[str, Any], int, List[str]]] = []

            for task in batch_tasks:
                iteration = task_order[task["id"]]
                run_set = _resolve_validators(task.get("validator_focus"), effective_validators_enabled)

                if resume and task["id"] in completed_task_ids:
                    _log_event(
                        "task.resume_skipped",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        iteration=iteration,
                        task_id=task["id"],
                        task_title=task["title"],
                        validator_focus=run_set,
                    )
                    quality_summary["tasks"].append(
                        {
                            "task_id": task["id"],
                            "status": "passed",
                            "attempts": 0,
                            "validator_focus": run_set,
                            "attempt_trend": [],
                            "hard_failures": 0,
                            "soft_failures": 0,
                            "last_validation": [],
                            "repair_passes": 0,
                            "quality_trace": [],
                            "resumed_from_checkpoint": True,
                        }
                    )
                    _write_json(
                        ws,
                        QUALITY_TASK_FILE_TMPL.format(task_id=task["id"]),
                        {
                            "task_id": task["id"],
                            "status": "passed",
                            "attempts": [],
                            "attempts_count": 0,
                            "validator_focus": run_set,
                            "attempt_trend": [],
                            "last_validation": [],
                            "repair_passes": 0,
                            "quality_trace": [],
                            "resumed_from_checkpoint": True,
                        },
                    )
                    _write_json(
                        ws,
                        QUALITY_TASK_LAST_FILE_TMPL.format(task_id=task["id"]),
                        {
                            "validator_focus": run_set,
                            "run_level": "per_task",
                            "validation": [],
                            "record_count": 0,
                            "resumed_from_checkpoint": True,
                        },
                    )
                    continue

                # Skip tasks whose dependencies failed or were skipped
                if continue_on_failure and (failed_task_ids or skipped_task_ids):
                    unmet_deps = [
                        dep for dep in task.get("depends_on", [])
                        if dep in failed_task_ids or dep in skipped_task_ids
                    ]
                    if unmet_deps:
                        skipped_task_ids.add(str(task["id"]))
                        _log_event(
                            "task.dependency_skipped",
                            run_id=run_id,
                            request_id=request_id,
                            profile=profile,
                            iteration=iteration,
                            task_id=task["id"],
                            task_title=task["title"],
                            unmet_dependencies=unmet_deps,
                            reason="dependency_task_failed_or_skipped",
                        )
                        trace.record(
                            EventType.TASK_SKIPPED,
                            task_id=task["id"],
                            unmet_deps=unmet_deps,
                        )
                        quality_summary["tasks"].append(
                            {
                                "task_id": task["id"],
                                "status": "skipped",
                                "attempts": 0,
                                "validator_focus": run_set,
                                "attempt_trend": [],
                                "hard_failures": 0,
                                "soft_failures": 0,
                                "last_validation": [],
                                "repair_passes": 0,
                                "quality_trace": [],
                                "skipped_reason": "dependency_failed",
                                "unmet_dependencies": unmet_deps,
                            }
                        )
                        _write_json(
                            ws,
                            QUALITY_TASK_FILE_TMPL.format(task_id=task["id"]),
                            {
                                "task_id": task["id"],
                                "status": "skipped",
                                "attempts": [],
                                "attempts_count": 0,
                                "validator_focus": run_set,
                                "attempt_trend": [],
                                "last_validation": [],
                                "repair_passes": 0,
                                "quality_trace": [],
                                "skipped_reason": "dependency_failed",
                                "unmet_dependencies": unmet_deps,
                            },
                        )
                        continue

                runnable.append((task, iteration, run_set))

            if not runnable:
                continue

            contains_global_validator = any(
                any(name in FULL_REPO_VALIDATORS for name in run_set)
                for _, _, run_set in runnable
            )

            if len(runnable) > 1 and not contains_global_validator:
                _log_event(
                    "task.batch_parallel_start",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    level=level_idx,
                    batch=batch_idx,
                    task_ids=[task["id"] for task, _, _ in runnable],
                    batch_size=len(runnable),
                    concurrency_limit=effective_parallel,
                )
                batch_results = []
                _chunks = schedule_batch_chunks(runnable, effective_parallel, _timing_store)
                for chunk in _chunks:
                    chunk_results = await asyncio.gather(
                        *[
                            _run_task_execution(task, iteration=iteration, run_set=run_set)
                            for task, iteration, run_set in chunk
                        ]
                    )
                    batch_results.extend(chunk_results)
                _log_event(
                    "task.batch_parallel_complete",
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    level=level_idx,
                    batch=batch_idx,
                    task_ids=[task["id"] for task, _, _ in runnable],
                    batch_size=len(runnable),
                    concurrency_limit=effective_parallel,
                )
            else:
                if len(runnable) > 1 and contains_global_validator:
                    _log_event(
                        "task.batch_parallel_skipped",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        level=level_idx,
                        batch=batch_idx,
                        task_ids=[task["id"] for task, _, _ in runnable],
                        reason="global_validators_detected",
                    )
                batch_results = []
                for task, iteration, run_set in runnable:
                    batch_results.append(await _run_task_execution(task, iteration=iteration, run_set=run_set))

            for result in sorted(batch_results, key=lambda row: int(row["iteration"])):
                quality_summary["tasks"].append(cast(Dict[str, Any], result["task_entry"]))
                last_validation = cast(List[Dict[str, Any]], result["last_validation"])

                if result["status"] != "passed":
                    task_failures += 1
                    unresolved.append(str(result["task_id"]))
                    failed_task_ids.add(str(result["task_id"]))

                    if not continue_on_failure:
                        # Legacy behaviour: stop immediately on first failure
                        _write_json(ws, QUALITY_SUMMARY_FILE, quality_summary)
                        _write_checkpoint(
                            ws,
                            sorted(completed_task_ids),
                            status="failed",
                            run_id=run_id,
                            request_id=request_id,
                            profile=profile,
                            failed_task_id=str(result["task_id"]),
                        )
                        progress.run_end(run_id, ok=False)
                        return False, prd_struct, plan, last_validation

                    # Resilient mode: record failure and continue
                    _log_event(
                        "task.failed_continuing",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        task_id=str(result["task_id"]),
                        task_failures_so_far=task_failures,
                        remaining_levels=len(task_levels) - level_idx,
                    )
                    trace.record(
                        EventType.TASK_FAILED_CONTINUING,
                        task_id=str(result["task_id"]),
                        task_failures_so_far=task_failures,
                    )
                    _write_checkpoint(
                        ws,
                        sorted(completed_task_ids),
                        status="running_with_failures",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                        failed_task_ids=sorted(failed_task_ids),
                        skipped_task_ids=sorted(skipped_task_ids),
                    )
                else:
                    completed_task_ids.add(str(result["task_id"]))
                    _write_checkpoint(
                        ws,
                        sorted(completed_task_ids),
                        status="running",
                        run_id=run_id,
                        request_id=request_id,
                        profile=profile,
                    )

            _write_json(ws, QUALITY_SUMMARY_FILE, quality_summary)

    # 4.5) Write change summary
    _change_summary = _write_change_summary(ws, _pre_existing_files, incremental_mode)
    _log_event(
        "run.change_summary",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        incremental_mode=incremental_mode,
        files_added=_change_summary["files_added_count"],
        files_modified=_change_summary["files_possibly_modified_count"],
        files_deleted=_change_summary["files_deleted_count"],
    )

    trace.end_phase("implementation")
    progress.phase_end("implementation")

    # 5) Final enterprise validation (all enabled)
    trace.start_phase("final_validation")
    progress.phase_start("final_validation")
    _log_event(
        "validation.phase_start",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        phase="final",
        validator_count=len(effective_validators_enabled),
    )
    final_perf_attempts = 0
    final_perf_repair_passed = False

    progress.validation_start("final", effective_validators_enabled)
    final_res = validators.run_all(
        effective_validators_enabled,
        audit_required=audit_required,
        soft_validators=final_soft,
        phase="final",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        task_id="final",
        iteration=0,
    )
    last_validation = Validators.serialize(final_res)
    final_ok = _validations_ok(last_validation, final_soft)
    progress.validation_end("final", ok=final_ok)

    failed_perf_rows = _perf_failure_rows(last_validation)
    while (
        not final_ok
        and failed_perf_rows
        and final_perf_attempts < 1
        and total_fix_loops < max_fix_loops_total
    ):
        final_perf_attempts += 1
        total_fix_loops += 1
        perf_targets = performance_context if isinstance(performance_context, dict) else {}
        perf_hotspots: list[str] = []
        latency_paths = perf_targets.get("latency_sensitive_paths")
        if isinstance(latency_paths, list):
            perf_hotspots = [str(p) for p in latency_paths]

        perf_retry_set = _targeted_perf_validator_set(
            failed_perf_rows=failed_perf_rows,
            available=effective_validators_enabled,
        )

        perf_task = {
            "id": "performance-hotspots",
            "title": "Performance-focused repair",
            "goal": "Fix performance gate regressions without broad changes.",
            "files": _perf_repair_task_files(plan, [str(p) for p in perf_hotspots]),
            "acceptance": ["Reduce or remove observed perf regressions on targeted paths"],
            "depends_on": [],
            "quality_expectations": {
                "requires_tests": False,
                "requires_error_contract": False,
                "touches_contract": False,
            },
            "validator_focus": perf_retry_set,
        }
        perf_payload = _build_task_payload(plan, perf_task, performance_context=performance_context)
        perf_payload["validation"] = last_validation
        perf_payload["performance_failures"] = failed_perf_rows
        if code_index.files:
            perf_payload["files_context"] = context_selector.select_for_task(
                goal="Fix performance failures",
                seed_files=cast(List[str], perf_task["files"]),
            )
        else:
            perf_payload["files_context"] = _build_files_context(ws, cast(List[str], perf_task["files"]))
        perf_payload["guidance"] = "Target only performance-flagged hotspots. Keep edits minimal and path-specific."

        _log_event(
            "validation.perf_fix_requested",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            phase="final",
            failure_count=len(failed_perf_rows),
            attempt=final_perf_attempts,
            perf_validator_set=perf_retry_set,
            perf_hotspot_count=len(perf_hotspots),
            targeted_file_count=len(perf_task["files"]),
        )

        _perf_fixer_system = p["fixer"]["system"]
        if incremental_mode:
            from .roles import INCREMENTAL_FIXER_ADDENDUM as _PERF_FIXER_ADD
            _perf_fixer_system += "\n" + _PERF_FIXER_ADD

        perf_fix = await _llm_json(
            client,
            _perf_fixer_system,
            json_dumps(perf_payload),
            CHANGESET_SCHEMA,
            max_repair=max_json_repair,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            component="perf_fixer",
            semantic_validator=_validate_handoff_fields,
            temperature=_role_temperature("perf_fixer"),
            role_hint="fixer",
        )
        for c in perf_fix["changes"]:
            ws.apply_changes([Change(op=c["op"], path=c["path"], content=c.get("content"))])

        final_res = validators.run_all(
            perf_retry_set,
            audit_required=audit_required,
            soft_validators=final_soft,
            phase="final",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id="final",
            iteration=final_perf_attempts,
        )
        last_validation = Validators.serialize(final_res)
        final_ok = _validations_ok(last_validation, final_soft)
        failed_perf_rows = _perf_failure_rows(last_validation)
        final_perf_repair_passed = final_ok

    unresolved.extend([t["task_id"] for t in quality_summary["tasks"] if t["status"] != "passed"])
    if not final_perf_repair_passed and failed_perf_rows and not final_ok:
        _log_event(
            "validation.perf_fix_exhausted",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            phase="final",
            failed_perf_count=len(failed_perf_rows),
            attempts=final_perf_attempts,
        )

    hard_counts = sum(task.get("hard_failures", 0) for task in quality_summary["tasks"])
    soft_counts = sum(task.get("soft_failures", 0) for task in quality_summary["tasks"])
    quality_summary["final"] = {
        "status": "passed" if final_ok else "failed",
        "soft_failures": sum(1 for r in last_validation if r["status"] == "soft_fail"),
        "hard_failures": sum(1 for r in last_validation if r["status"] == "failed"),
        "validator_focus": effective_validators_enabled,
        "validations": last_validation,
        "blocking_failures": sum(
            1 for r in last_validation if r["name"] not in final_soft and not r["ok"]
        ),
    }

    _log_event(
        "validation.final_summary",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        phase="final",
        status="passed" if final_ok else "failed",
        validation_count=len(last_validation),
        blocking_failures=quality_summary["final"]["blocking_failures"],
        soft_failures=quality_summary["final"]["soft_failures"],
        hard_failures=quality_summary["final"]["hard_failures"],
    )

    final_phase_passed = final_ok
    if not final_phase_passed:
        unresolved.append("final_validation")

    unresolved = sorted(set(unresolved))
    quality_summary["unresolved_blockers"] = unresolved

    quality_summary["totals"] = {
        "tasks": len(tasks),
        "successful_tasks": sum(1 for t in quality_summary["tasks"] if t.get("status") == "passed"),
        "repair_passes": sum(int(bool(t.get("repair_passes"))) for t in quality_summary["tasks"]),
        "total_task_attempts": sum(int(t.get("attempts", 0)) for t in quality_summary["tasks"]),
        "resolved_tasks": len(quality_summary["tasks"]) - task_failures - len(skipped_task_ids),
        "hard_failures": hard_counts,
        "soft_failures": soft_counts,
        "unresolved_tasks": len(unresolved),
        "max_fix_loops_reached": total_fix_loops >= max_fix_loops_total,
        "skipped_tasks": len(skipped_task_ids),
        "failed_task_ids": sorted(failed_task_ids),
        "skipped_task_ids": sorted(skipped_task_ids),
    }
    quality_summary["task_validation_trend"] = [
        {
            "task_id": t["task_id"],
            "status": t["status"],
            "attempts": t["attempts"],
            "hard_failures": t.get("hard_failures", 0),
            "soft_failures": t.get("soft_failures", 0),
        }
        for t in quality_summary["tasks"]
    ]

    quality_summary["quality_payload_files"] = {
        "task_quality_index": QUALITY_SUMMARY_FILE,
        "task_profile": QUALITY_PROFILE_FILE,
        "quality_summary": QUALITY_SUMMARY_METADATA_FILE,
        "quality_resolution": QUALITY_RESOLUTION_FILE,
        "final_last_validation": QUALITY_TASK_LAST_FILE_TMPL.format(task_id="final"),
    }

    _write_json(ws, QUALITY_SUMMARY_FILE, quality_summary)
    _write_json(ws, QUALITY_RESOLUTION_FILE, {
        "quality_profile": quality_profile,
        "task_soft_fail": sorted(task_soft),
        "final_soft_fail": sorted(final_soft),
        "task_validator_budget": effective_validators_enabled,
    })

    _write_json(ws, QUALITY_TASK_LAST_FILE_TMPL.format(task_id="final"), {
        "validation": last_validation,
        "validator_focus": effective_validators_enabled,
        "run_level": "final",
    })

    _write_json(ws, REPAIR_HISTORY_FILE, repair_history.to_dict())

    trace.end_phase("final_validation")
    progress.phase_end("final_validation")
    trace.record(
        EventType.RUN_COMPLETED,
        ok=final_ok and task_failures == 0,
        total_fix_loops=total_fix_loops,
        unresolved_count=len(unresolved),
    )
    trace_data = trace.to_dict()
    _write_json(ws, RUN_TRACE_FILE, trace_data)

    # -- Context cache summary -------------------------------------------------
    _cache_savings = _incremental_cache.get_cumulative_savings()
    if _cache_savings.chars_saved > 0:
        trace.record(
            EventType.CONTEXT_CACHE_SUMMARY,
            total_chars_saved=_cache_savings.chars_saved,
            total_savings_pct=round(_cache_savings.savings_pct, 1),
            files_stubbed=_cache_savings.files_unchanged,
        )

    # -- Performance baseline tracking ----------------------------------------
    _task_timings = collect_task_timings(quality_summary)
    try:
        _perf_result = _perf_baseline_check(
            ws_root=ws.root,
            run_id=run_id,
            profile=profile,
            trace_dict=trace_data,
            quality_summary=quality_summary,
            quality_profile=quality_profile,
            task_timings=_task_timings,
        )
        _log_event(
            "perf_baseline.checked",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            has_baseline=_perf_result.has_baseline,
            regression_detected=not _perf_result.ok,
        )
    except Exception:
        logger.debug("perf_baseline: failed to record/check", exc_info=True)

    progress.run_end(run_id, ok=final_ok and task_failures == 0)

    _log_event(
        "run.completed",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        status="passed" if (final_ok and task_failures == 0) else "failed",
        final_ok=final_ok,
        task_failures=task_failures,
        total_fix_loops=total_fix_loops,
        unresolved_count=len(unresolved),
    )
    _write_checkpoint(
        ws,
        sorted(completed_task_ids),
        status="completed" if (final_ok and task_failures == 0) else "failed",
        run_id=run_id,
        request_id=request_id,
        profile=profile,
        failed_task_id=None if (final_ok and task_failures == 0) else "final_validation",
        failed_task_ids=sorted(failed_task_ids),
        skipped_task_ids=sorted(skipped_task_ids),
    )

    return (
        final_ok and task_failures == 0,
        prd_struct,
        plan,
        last_validation,
    )
