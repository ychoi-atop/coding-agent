from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
from datetime import datetime
from pathlib import PurePosixPath
import os
import time
from typing import Any, Callable, Dict, List, Set, Tuple, cast
from uuid import uuid4

from jsonschema import validate  # type: ignore[import-untyped]

from .llm_client import LLMClient
from .json_utils import strict_json_loads, json_dumps
from .roles import prompts
from .schemas import PRD_SCHEMA, PLAN_SCHEMA, CHANGESET_SCHEMA, ARCHITECTURE_SCHEMA, REVIEW_SCHEMA, PRD_ANALYSIS_SCHEMA
from .workspace import Workspace, Change
from .exec_kernel import ExecKernel
from .env_manager import EnvManager
from .validators import Validators

logger = logging.getLogger("autodev")


def _log_event(event: str, run_id: str, request_id: str, profile: str | None = None, **fields: object) -> None:
    payload = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": event,
        "run_id": run_id,
        "request_id": request_id,
        "run_profile": profile,
        **fields,
    }

    if logger.handlers:
        logger.info(json_dumps(payload))
    else:
        print(json_dumps(payload))


def _safe_short_text(value: str, limit: int = 200) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

DEFAULT_TASK_SOFT_VALIDATORS = {"docker_build", "pip_audit", "sbom", "semgrep"}
DEFAULT_VALIDATOR_FALLBACK = [
    "ruff",
    "mypy",
    "pytest",
    "pip_audit",
    "bandit",
    "semgrep",
    "sbom",
    "docker_build",
]
QUALITY_SUMMARY_FILE = ".autodev/task_quality_index.json"
QUALITY_TASK_FILE_TMPL = ".autodev/task_{task_id}_quality.json"
QUALITY_TASK_LAST_FILE_TMPL = ".autodev/task_{task_id}_last_validation.json"
QUALITY_PROFILE_FILE = ".autodev/quality_profile.json"
QUALITY_SUMMARY_METADATA_FILE = ".autodev/quality_run_summary.json"
QUALITY_RESOLUTION_FILE = ".autodev/quality_resolution.json"
PLAN_CACHE_FILE = ".autodev/generate_cache.json"
CHECKPOINT_FILE = ".autodev/checkpoint.json"
PLAN_CACHE_VERSION = 1
FULL_REPO_VALIDATORS = {
    "mypy",
    "pytest",
    "pip_audit",
    "bandit",
    "semgrep",
    "sbom",
    "docker_build",
    "dependency_lock",
}

HANDOFF_REQUIRED_FIELDS = [
    "Summary",
    "Changed Files",
    "Commands",
    "Evidence",
    "Risks",
    "Next Input",
]
DEFAULT_MAX_PARALLEL_TASKS = 2
RECOMMENDED_MAX_PARALLEL_TASKS = 3
CONSECUTIVE_FAILURE_FAIL_FAST_THRESHOLD = 3


def _resolve_gate_profile(
    quality_profile: Dict[str, Any] | None,
    gate_profile: str | None,
) -> Dict[str, Any]:
    if quality_profile is None or not gate_profile:
        out = dict(quality_profile) if quality_profile else {}
        out.setdefault("resolved_from", gate_profile or out.get("name", "balanced"))
        return out

    by_level = quality_profile.get("by_level")
    if not isinstance(by_level, dict):
        out = dict(quality_profile)
        out["name"] = gate_profile
        out["resolved_from"] = gate_profile
        return out

    overrides = by_level.get(gate_profile)
    if not isinstance(overrides, dict):
        out = dict(quality_profile)
        out.setdefault("name", out.get("name", gate_profile))
        out["resolved_from"] = gate_profile
        return out

    merged = {k: v for k, v in quality_profile.items() if k not in {"by_level", "name", "resolved_from"}}
    merged.update(overrides)
    merged["name"] = gate_profile
    merged["resolved_from"] = gate_profile
    return merged


def _msg(system: str, user: str):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _ordered_unique(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _resolve_validators(focus: List[str] | None, validators_enabled: List[str]) -> List[str]:
    enabled = [v for v in validators_enabled if v in DEFAULT_VALIDATOR_FALLBACK]
    if focus:
        selected = [v for v in _ordered_unique(focus) if v in validators_enabled]
        if selected:
            return selected

    selected = [v for v in DEFAULT_VALIDATOR_FALLBACK if v in enabled]
    if selected:
        return selected
    return _ordered_unique(validators_enabled)


def _failure_signature(validation_rows: List[Dict[str, Any]]) -> tuple:
    failers = [
        (row["name"], row.get("status", "unknown"), row.get("error_classification") or "")
        for row in validation_rows
        if not row["ok"]
    ]
    return tuple(failers)


def _failed_validator_names(validation_rows: List[Dict[str, Any]]) -> List[str]:
    return [row["name"] for row in validation_rows if not row["ok"]]


def _merge_validation_rows(
    previous: List[Dict[str, Any]],
    fresh: List[Dict[str, Any]],
    run_set: List[str],
) -> List[Dict[str, Any]]:
    by_name = {row["name"]: row for row in previous}
    fresh_by_name = {row["name"]: row for row in fresh}

    merged: List[Dict[str, Any]] = []
    for name in run_set:
        if name in fresh_by_name:
            merged.append(fresh_by_name[name])
        elif name in by_name:
            merged.append(by_name[name])

    existing = {row["name"] for row in merged}
    for row in fresh_by_name.values():
        if row["name"] not in existing:
            merged.append(row)

    return merged


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def _generation_cache_key(
    prd_markdown: str,
    template_candidates: List[str],
    validators_enabled: List[str],
    quality_profile: Dict[str, Any] | None,
) -> str:
    key_payload = {
        "version": PLAN_CACHE_VERSION,
        "prd_sha256": hashlib.sha256((prd_markdown or "").encode("utf-8")).hexdigest(),
        "template_candidates": template_candidates,
        "validators": validators_enabled,
        "quality_profile": quality_profile or {},
    }
    return _hash_payload(key_payload)


def _read_generation_cache(ws: Workspace) -> Dict[str, Any] | None:
    if not ws.exists(PLAN_CACHE_FILE):
        return None
    try:
        payload = strict_json_loads(ws.read_text(PLAN_CACHE_FILE))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != PLAN_CACHE_VERSION:
        return None
    return payload


def _write_generation_cache(
    ws: Workspace,
    cache_key: str,
    prd_struct: Dict[str, Any],
    plan: Dict[str, Any],
    architecture: Dict[str, Any] | None = None,
    prd_analysis: Dict[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "version": PLAN_CACHE_VERSION,
        "cache_key": cache_key,
        "prd_struct": prd_struct,
        "plan": plan,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    if architecture is not None:
        payload["architecture"] = architecture
    if prd_analysis is not None:
        payload["prd_analysis"] = prd_analysis
    ws.write_text(PLAN_CACHE_FILE, json_dumps(payload))


def _read_checkpoint(ws: Workspace) -> Dict[str, Any] | None:
    if not ws.exists(CHECKPOINT_FILE):
        return None
    try:
        payload = strict_json_loads(ws.read_text(CHECKPOINT_FILE))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_checkpoint(
    ws: Workspace,
    completed_task_ids: List[str],
    *,
    status: str,
    run_id: str,
    request_id: str,
    profile: str | None = None,
    failed_task_id: str | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "status": status,
        "completed_task_ids": sorted(set(completed_task_ids)),
        "failed_task_id": failed_task_id,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "run_id": run_id,
        "request_id": request_id,
        "profile": profile,
    }
    _write_json(ws, CHECKPOINT_FILE, payload)


def _build_task_payload(
    plan: Dict[str, Any],
    task: Dict[str, Any],
    performance_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    constraints: List[str] = []
    acceptance = task.get("acceptance", [])
    if isinstance(acceptance, list) and acceptance:
        constraints.extend([f"acceptance: {item}" for item in acceptance if isinstance(item, str) and item.strip()])

    quality_expectations = task.get("quality_expectations", {})
    if isinstance(quality_expectations, dict) and quality_expectations:
        constraints.append("quality_expectations를 만족해야 함")

    output_format = {
        "type": "CHANGESET_SCHEMA",
        "required_root_fields": ["role", "summary", "changes", "notes", "handoff"],
        "handoff_required_fields": HANDOFF_REQUIRED_FIELDS,
    }

    return {
        "core": {
            "goal": task.get("goal", ""),
            "paths": task.get("files", []),
            "constraints": constraints,
            "output_format": output_format,
        },
        "optional_context": {
            "task": {
                "id": task["id"],
                "title": task["title"],
                "acceptance": acceptance,
                "depends_on": task.get("depends_on", []),
                "quality_expectations": quality_expectations,
                "validator_focus": task.get("validator_focus", []),
            },
            "plan": {
                "project": {
                    "type": plan["project"].get("type"),
                    "name": plan["project"].get("name"),
                    "quality_gate_profile": plan["project"].get("quality_gate_profile"),
                    "default_artifacts": plan["project"].get("default_artifacts", []),
                },
                "performance_hints": performance_context or {},
            },
        },
    }


def _extract_performance_hints(prd_struct: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    hints: Dict[str, Any] = {}

    for key in ("performance_targets", "expected_load", "latency_sensitive_paths", "cost_priority"):
        if key in plan and isinstance(plan[key], (dict, list, str)):
            hints[key] = plan[key]

    fallback = {
        "performance_targets": prd_struct.get("performance_targets"),
        "expected_load": prd_struct.get("expected_load"),
        "latency_sensitive_paths": prd_struct.get("latency_sensitive_paths"),
        "cost_priority": prd_struct.get("cost_priority"),
    }
    for key, value in fallback.items():
        if key not in hints and value not in (None, {}, [], ""):
            hints[key] = value

    return hints


def _is_perf_gate_failure(row: Dict[str, Any]) -> bool:
    if row.get("ok"):
        return False
    name = str(row.get("name", "")).lower()
    error_class = str(row.get("error_classification") or "").lower()
    tokens = ("perf", "performance", "latency", "throughput")
    return any(token in name for token in tokens) or any(token in error_class for token in tokens)


def _perf_failure_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if _is_perf_gate_failure(row)]


def _perf_repair_task_files(plan: Dict[str, Any], hotspots: List[str]) -> List[str]:
    if not hotspots:
        return []

    candidate_files = []
    for t in plan.get("tasks", []):
        for fp in t.get("files", []):
            if any(path in fp for path in hotspots):
                candidate_files.append(fp)

    if candidate_files:
        return list(dict.fromkeys(candidate_files))

    return [fp for task in plan.get("tasks", []) for fp in task.get("files", [])][:12]


def _targeted_perf_validator_set(
    failed_perf_rows: List[Dict[str, Any]],
    available: List[str],
) -> List[str]:
    perf_names = [row["name"] for row in failed_perf_rows if isinstance(row.get("name"), str)]
    if not perf_names:
        return available

    available_set = {name for name in available}
    targeted = [name for name in perf_names if name in available_set]
    if targeted:
        return targeted
    return available


def _build_validator_counts(validation_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {"passed": 0, "failed": 0, "soft_fail": 0}
    for row in validation_rows:
        status = row.get("status", "failed")
        out[status] = out.get(status, 0) + 1
    return out


def _build_pass_map(validation_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        row["name"]: {
            "ok": bool(row["ok"]),
            "status": row.get("status", "failed"),
            "returncode": row.get("returncode", 1),
            "duration_ms": row.get("duration_ms", 0),
        }
        for row in validation_rows
    }


def _build_quality_row(
    task_id: str,
    attempt: int,
    run_set: List[str],
    validation_rows: List[Dict[str, Any]],
    duration_ms: int,
    soft_validators: Set[str],
    all_ok: bool,
    quality_notes: List[str] | None = None,
    validation_links: Dict[str, Any] | None = None,
    repair_pass: bool = False,
) -> Dict[str, Any]:
    blocked = [row for row in validation_rows if row["name"] not in soft_validators]
    hard_failures = sum(1 for row in blocked if not row["ok"])
    soft_failures = sum(1 for row in validation_rows if row["name"] in soft_validators and not row["ok"])

    return {
        "task_id": task_id,
        "attempt": attempt,
        "validator_focus": run_set,
        "duration_ms": duration_ms,
        "status": "passed" if all_ok else "failed",
        "repair_pass": repair_pass,
        "quality_notes": quality_notes or [],
        "validation_links": validation_links or {},
        "validator_counts": _build_validator_counts(validation_rows),
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "pass_fail_map": _build_pass_map(validation_rows),
        "validations": validation_rows,
    }


def _toposort(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {t["id"]: t for t in tasks}
    indeg = {t["id"]: 0 for t in tasks}
    graph: Dict[str, List[str]] = {t["id"]: [] for t in tasks}

    for t in tasks:
        for dep in t["depends_on"]:
            if dep in graph:
                graph[dep].append(t["id"])
                indeg[t["id"]] += 1

    q = [tid for tid, d in indeg.items() if d == 0]
    out = []
    while q:
        tid = q.pop(0)
        out.append(by_id[tid])
        for nxt in graph[tid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)

    if len(out) == len(tasks):
        return out

    state: Dict[str, int] = {}
    stack: List[str] = []
    cycle: List[str] = []

    def dfs(tid: str) -> bool:
        nonlocal cycle
        state[tid] = 1
        stack.append(tid)
        for nxt in graph[tid]:
            if state.get(nxt, 0) == 0:
                if dfs(nxt):
                    return True
            elif state.get(nxt) == 1:
                cycle_start = stack.index(nxt)
                cycle = stack[cycle_start:] + [nxt]
                return True
        stack.pop()
        state[tid] = 2
        return False

    for tid in out:
        state.setdefault(tid, 2)
    for tid in indeg:
        if state.get(tid, 0) == 0:
            if dfs(tid) and cycle:
                break

    if not cycle:
        unresolved = [t["id"] for t in tasks if indeg[t["id"]] > 0]
        cycle = unresolved

    raise ValueError(
        "Dependency cycle detected in task graph. Resolve dependency loop before execution. "
        f"Cycle path: {' -> '.join(cycle)}"
    )


def _toposort_levels(ordered_tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    levels: Dict[int, List[Dict[str, Any]]] = {}
    level_by_id: Dict[str, int] = {}

    for task in ordered_tasks:
        dep_levels = [level_by_id[dep] for dep in task["depends_on"] if dep in level_by_id]
        level = (max(dep_levels) + 1) if dep_levels else 0
        level_by_id[task["id"]] = level
        levels.setdefault(level, []).append(task)

    return [levels[idx] for idx in sorted(levels)]


def _task_file_set(task: Dict[str, Any]) -> Set[str]:
    files = task.get("files", [])
    if not isinstance(files, list):
        return set()
    return {str(fp).replace("\\", "/") for fp in files if isinstance(fp, str)}


def _partition_level_for_parallel(level_tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    batches: List[List[Dict[str, Any]]] = []
    batch_files: List[Set[str]] = []

    for task in level_tasks:
        files = _task_file_set(task)
        placed = False
        for idx, used_files in enumerate(batch_files):
            if files.isdisjoint(used_files):
                batches[idx].append(task)
                used_files.update(files)
                placed = True
                break
        if not placed:
            batches.append([task])
            batch_files.append(set(files))
    return batches


def _is_glob_pattern(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _match_task_file_pattern(pattern: str, repo_files: List[str]) -> List[str]:
    pat = pattern.replace("\\", "/")
    variants = [pat]
    if pat.startswith("**/"):
        variants.append(pat[3:])
    if "/**/" in pat:
        variants.append(pat.replace("/**/", "/"))

    out: List[str] = []
    for rel in repo_files:
        rel_norm = rel.replace("\\", "/")
        rel_path = PurePosixPath(rel_norm)
        for cand in variants:
            if fnmatch.fnmatch(rel_norm, cand) or rel_path.match(cand):
                out.append(rel_norm)
                break
            if "/" not in cand and fnmatch.fnmatch(os.path.basename(rel_norm), cand):
                out.append(rel_norm)
                break
    return sorted(set(out))


def _canonicalize_task_files(plan: Dict[str, Any], repo_files: List[str]) -> Dict[str, Any]:
    for t in plan["tasks"]:
        resolved: List[str] = []
        for fp in t["files"]:
            rel = fp.replace("\\", "/")
            if _is_glob_pattern(rel):
                matches = _match_task_file_pattern(rel, repo_files)
                if not matches:
                    raise ValueError(f"Task '{t['id']}' has unmatched file glob: {fp}")
                resolved.extend(matches)
            else:
                resolved.append(rel)
        t["files"] = list(dict.fromkeys(resolved))
    return plan


def _build_files_context(
    ws: Workspace,
    files: List[str],
    max_files: int = 12,
    max_chars_per_file: int = 8_000,
) -> Dict[str, str]:
    files_ctx: Dict[str, str] = {}
    for fp in files[:max_files]:
        if ws.exists(fp):
            try:
                files_ctx[fp] = ws.read_text(fp)[:max_chars_per_file]
            except Exception:
                files_ctx[fp] = "<unreadable>"
        else:
            files_ctx[fp] = "<missing>"
    return files_ctx


def _validations_ok(validation_rows: List[Dict[str, Any]], soft_validators: set[str]) -> bool:
    blocking = [row for row in validation_rows if row["name"] not in soft_validators]
    return all(row["ok"] for row in blocking)


def _resolve_soft_fail(
    profile_section: Dict[str, Any] | None,
    explicit: List[str] | None,
    compact_key: str | None = None,
) -> Set[str]:
    if explicit is not None:
        return set(explicit)
    if not profile_section:
        return set()

    if compact_key and isinstance(profile_section, dict):
        compact_values = profile_section.get(compact_key)
        if compact_values is not None:
            profile_section = {"soft_fail": compact_values}

    values = profile_section.get("soft_fail")
    if isinstance(values, list):
        return set(values)
    return set()


def _resolve_repeat_failure_guard(quality_profile: Dict[str, Any] | None) -> Dict[str, Any]:
    defaults = {"enabled": True, "max_retries_before_targeted_fix": 1}
    if not isinstance(quality_profile, dict):
        return defaults

    escalation = quality_profile.get("escalation")
    if not isinstance(escalation, dict):
        return defaults

    guard = escalation.get("repeat_failure_guard")
    if not isinstance(guard, dict):
        return defaults

    enabled = guard.get("enabled", defaults["enabled"])
    max_retries = guard.get(
        "max_retries_before_targeted_fix",
        defaults["max_retries_before_targeted_fix"],
    )

    if not isinstance(enabled, bool):
        enabled = defaults["enabled"]
    if not isinstance(max_retries, int) or max_retries < 0:
        max_retries = defaults["max_retries_before_targeted_fix"]

    return {
        "enabled": enabled,
        "max_retries_before_targeted_fix": max_retries,
    }


def _build_task_summary_rows(
    attempts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    trend = []
    for row in attempts:
        trend.append(
            {
                "attempt": row["attempt"],
                "status": row["status"],
                "hard_failures": row["hard_failures"],
                "soft_failures": row["soft_failures"],
                "duration_ms": row["duration_ms"],
            }
        )
    return trend


def _summarize_run(profile: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "quality_profile": profile,
        "project_type": plan["project"].get("type"),
        "quality_gate_profile": plan["project"].get("quality_gate_profile"),
        "generated_tasks": [t["id"] for t in plan.get("tasks", [])],
        "default_artifacts": plan["project"].get("default_artifacts", []),
    }


def _write_json(ws: Workspace, rel_path: str, payload: Dict[str, Any]) -> None:
    ws.write_text(rel_path, json_dumps(payload))


def _validate_handoff_fields(changeset: Dict[str, Any]) -> str | None:
    handoff = changeset.get("handoff")
    if not isinstance(handoff, dict):
        return "MISSING_HANDOFF_FIELDS:" + ",".join(HANDOFF_REQUIRED_FIELDS)

    missing = [field for field in HANDOFF_REQUIRED_FIELDS if not str(handoff.get(field, "")).strip()]
    if missing:
        return "MISSING_HANDOFF_FIELDS:" + ",".join(missing)
    return None


def _quality_metadata_from_changeset(
    changeset: Dict[str, Any],
    task_id: str,
    run_set: List[str],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    notes = changeset.get("quality_notes")
    links = changeset.get("validation_links")
    if isinstance(notes, list):
        out["quality_notes"] = notes
    if isinstance(links, dict):
        out["validation_links"] = links
    if "validation_links" not in out:
        out["validation_links"] = {
            "acceptance": [],
            "tasks": [task_id],
            "validators": run_set,
        }
    if "quality_notes" not in out:
        out["quality_notes"] = []
    handoff = changeset.get("handoff")
    if isinstance(handoff, dict):
        out["handoff"] = handoff
    return out


def _shorten_text(value: str, limit: int = 1400) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 5] + " ..."


def _coerce_prd_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Lightly fill missing optional-but-required-in-schema PRD fields.

    This keeps the schema happy for benchmark-style PRDs while preserving
    all model-provided details.
    """
    out = dict(data)
    out.setdefault("non_goals", [])
    out.setdefault("constraints", [])
    out.setdefault("nfr", {})
    out.setdefault("features", [])
    out.setdefault("acceptance_criteria", [])
    out.setdefault("goals", [])
    out.setdefault("title", "AutoDev PRD")

    if not isinstance(out.get("features"), list):
        out["features"] = []
    else:
        normalized_features = []
        for feature in out.get("features", []):
            if not isinstance(feature, dict):
                continue
            name = feature.get("name")
            if not isinstance(name, str) or not name.strip():
                fallback_name = feature.get("title")
                feature["name"] = fallback_name.strip() if isinstance(fallback_name, str) else "Feature"
            description = feature.get("description")
            if not isinstance(description, str) or not description.strip():
                if isinstance(feature.get("goal"), str) and feature["goal"].strip():
                    description = feature["goal"]
                elif isinstance(feature.get("summary"), str) and feature["summary"].strip():
                    description = feature["summary"]
                feature["description"] = description or "No description provided."
            requirements = feature.get("requirements")
            if not isinstance(requirements, list):
                requirements = []
            normalized_requirements: list[str] = []
            for req in requirements:
                if isinstance(req, str) and req.strip():
                    normalized_requirements.append(req)
                elif isinstance(req, dict) and isinstance(req.get("description"), str) and req["description"].strip():
                    normalized_requirements.append(req["description"])
            if not normalized_requirements:
                if isinstance(feature.get("description"), str) and feature["description"].strip():
                    normalized_requirements = [f"{feature['description']}"]
                else:
                    normalized_requirements = ["Implement feature."]
            feature["requirements"] = normalized_requirements
            normalized_features.append(feature)
        out["features"] = normalized_features

    return out


def _coerce_plan_payload(data: Dict[str, Any], template_candidates: List[str] | None = None) -> Dict[str, Any]:
    """Fill/normalize PLAN_SCHEMA required keys when model output is incomplete.

    This is intentionally conservative and keeps execution going for short
    benchmarking runs by inferring stable defaults.
    """
    allowed_top = {
        "project",
        "runtime_dependencies",
        "dev_dependencies",
        "tasks",
        "ci",
        "docker",
        "security",
        "observability",
        "performance_targets",
        "expected_load",
        "latency_sensitive_paths",
        "cost_priority",
    }
    out: Dict[str, Any] = {k: v for k, v in data.items() if k in allowed_top}

    out.setdefault("project", {})
    out.setdefault("tasks", [])
    out.setdefault("ci", {"enabled": True, "provider": "github_actions"})
    out.setdefault("docker", {"enabled": False})
    out.setdefault("security", {"enabled": False, "tools": []})
    out.setdefault("observability", {"enabled": False})

    project = dict(out.get("project", {}))
    template_root_default = (template_candidates or ["python_cli"])[0]
    valid_types = set(template_candidates or []) | {"python_fastapi", "python_cli", "python_library"}
    if project.get("type") not in valid_types:
        project["type"] = template_root_default
    if not isinstance(project.get("name"), str) or not project.get("name"):
        project["name"] = "autodev-bench"
    # python_version is optional for non-Python templates; provide default for Python.
    proj_type = project.get("type", "")
    if proj_type.startswith("python"):
        if not isinstance(project.get("python_version"), str) or not project.get("python_version"):
            project["python_version"] = "3.11"
    out["project"] = project

    ci = dict(out.get("ci", {}))
    ci.setdefault("enabled", True)
    ci.setdefault("provider", "github_actions")
    out["ci"] = ci

    docker = dict(out.get("docker", {}))
    docker.setdefault("enabled", False)
    out["docker"] = docker

    security = dict(out.get("security", {}))
    security.setdefault("enabled", False)
    security.setdefault("tools", [])
    if not isinstance(security.get("tools"), list):
        security["tools"] = []
    out["security"] = security

    observability = dict(out.get("observability", {}))
    observability.setdefault("enabled", False)
    out["observability"] = observability

    if "runtime_dependencies" not in out or not isinstance(out.get("runtime_dependencies"), list):
        out["runtime_dependencies"] = []
    if "dev_dependencies" not in out or not isinstance(out.get("dev_dependencies"), list):
        out["dev_dependencies"] = []

    normalized_tasks: List[Dict[str, Any]] = []
    raw_tasks = out.get("tasks", [])
    if isinstance(raw_tasks, list):
        for idx, raw_task in enumerate(raw_tasks, start=1):
            if not isinstance(raw_task, dict):
                continue

            title = str(raw_task.get("title") or raw_task.get("name") or f"Task {idx}").strip()
            goal = str(raw_task.get("goal") or raw_task.get("description") or "Implement requested behavior.").strip()
            if len(title) < 5:
                title = f"{title} work"

            raw_files = raw_task.get("files", [])
            files: List[str] = []
            if isinstance(raw_files, list):
                for item in raw_files:
                    if isinstance(item, str):
                        files.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("path"), str):
                        files.append(item.get("path"))
            if not files:
                files = ["README.md"]

            raw_acceptance = raw_task.get("acceptance", [])
            acceptance: List[str] = []
            if isinstance(raw_acceptance, list):
                acceptance = [str(x) for x in raw_acceptance if isinstance(x, str) and len(x.strip()) >= 5]
            if not acceptance:
                acceptance = ["Task implemented with automated checks and validation."]

            raw_depends_on = raw_task.get("depends_on", [])
            depends_on: List[str] = []
            if isinstance(raw_depends_on, list):
                depends_on = [str(x) for x in raw_depends_on if isinstance(x, str) and x.strip()]

            quality_expectations = raw_task.get("quality_expectations")
            if not isinstance(quality_expectations, dict):
                quality_expectations = {"requires_tests": False, "requires_error_contract": False}
            quality_expectations.setdefault("requires_tests", True)
            quality_expectations.setdefault("requires_error_contract", False)

            normalized_tasks.append(
                {
                    "id": str(raw_task.get("id") or f"task{len(normalized_tasks)+1}"),
                    "title": title,
                    "goal": goal,
                    "acceptance": acceptance,
                    "files": files,
                    "depends_on": depends_on,
                    "quality_expectations": {
                        "requires_tests": bool(quality_expectations.get("requires_tests")),
                        "requires_error_contract": bool(quality_expectations.get("requires_error_contract")),
                    },
                }
            )
    out["tasks"] = normalized_tasks
    return out


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
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    profile: str | None = None,
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
    )

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
    else:
        # 0) Analyze PRD quality (optional — skipped if role not defined)
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

        # 2) Architecture design (optional — skipped if role not defined)
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

        # 3) Plan
        planner_input: Dict[str, Any] = {
            "template_candidates": template_candidates,
            "prd_struct": prd_struct,
            "task": p["planner"]["task"],
        }
        if architecture is not None:
            planner_input["architecture"] = architecture
        plan = await _llm_json(
            client,
            p["planner"]["system"],
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
            p["planner"]["system"],
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
            return False, prd_struct, plan, []

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
    performance_context = _extract_performance_hints(prd_struct=prd_struct, plan=plan)
    task_order = {task["id"]: index for index, task in enumerate(tasks, start=1)}
    task_levels = _toposort_levels(tasks)
    task_context_cache: Dict[str, Dict[str, str]] = {}

    def _cached_files_context(task_id: str, files: List[str]) -> Dict[str, str]:
        cache_key = f"{task_id}:{'|'.join(files)}"
        cached = task_context_cache.get(cache_key)
        if cached is not None:
            return cached
        ctx = _build_files_context(ws, files)
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

        files_ctx = _cached_files_context(task["id"], task["files"])
        attempt_records: List[Dict[str, Any]] = []
        loops = 0
        last_failure_signature: tuple[Any, ...] = tuple()
        repeat_failure_count = 0
        consecutive_failures = 0
        repair_used = False
        previous_validation_rows: List[Dict[str, Any]] = []

        impl_payload = _build_task_payload(plan, task, performance_context=performance_context)
        impl_payload["files_context"] = files_ctx
        impl_payload["guidance"] = p["implementer"]["task"]
        changeset = await _llm_json(
            client,
            p["implementer"]["system"],
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
                return {
                    "task_id": task["id"],
                    "iteration": iteration,
                    "status": "failed",
                    "task_entry": task_entry,
                    "last_validation": task_last_validation,
                }

            files_ctx = _cached_files_context(task["id"], task["files"])
            same_failure_signature = bool(signature) and signature == last_failure_signature
            if same_failure_signature:
                repeat_failure_count += 1
            else:
                repeat_failure_count = 0
            repair_mode = "normal"
            targeted_fix_requested = False
            if not repair_used and repeat_guard_enabled:
                if repeat_guard_max_retries == 0:
                    targeted_fix_requested = True
                elif same_failure_signature and repeat_failure_count >= repeat_guard_max_retries:
                    targeted_fix_requested = True

            if targeted_fix_requested:
                repair_mode = "targeted"
                repair_payload = _build_task_payload(plan, task, performance_context=performance_context)
                repair_payload["files_context"] = files_ctx
                repair_payload["validation"] = task_last_validation
                repair_payload["guidance"] = (
                    "Do a task-level repair pass before targeted fix cycles. Address same failure pattern."
                )
                repair_used = True
            else:
                repair_payload = _build_task_payload(plan, task, performance_context=performance_context)
                repair_payload["validation"] = task_last_validation
                repair_payload["files_context"] = files_ctx
                repair_payload["guidance"] = p["fixer"]["task"]
            last_failure_signature = signature

            _log_event(
                "task.repair_requested",
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                iteration=iteration,
                task_id=task["id"],
                attempt=loops + 1,
                repair_mode=repair_mode,
                failure_signature=str(signature)[:200],
                repeat_failure_count=repeat_failure_count,
                repeat_failure_guard_enabled=repeat_guard_enabled,
                repeat_failure_guard_retries=repeat_guard_max_retries,
            )

            fix = await _llm_json(
                client,
                p["fixer"]["system"],
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
        return {
            "task_id": task["id"],
            "iteration": iteration,
            "status": "passed",
            "task_entry": task_entry,
            "last_validation": attempt_records[-1]["validations"],
        }

    for level_idx, level_tasks in enumerate(task_levels, start=1):
        level_batches = _partition_level_for_parallel(level_tasks)
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
                    concurrency_limit=max_parallel_tasks,
                )
                batch_results = []
                for chunk_start in range(0, len(runnable), max_parallel_tasks):
                    chunk = runnable[chunk_start : chunk_start + max_parallel_tasks]
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
                    concurrency_limit=max_parallel_tasks,
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
                    return False, prd_struct, plan, last_validation

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

    # 5) Final enterprise validation (all enabled)
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

        perf_fix = await _llm_json(
            client,
            p["fixer"]["system"],
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

    unresolved.extend([t["id"] for t in quality_summary["tasks"] if t["status"] != "passed"])
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
        "resolved_tasks": len(quality_summary["tasks"]) - task_failures,
        "hard_failures": hard_counts,
        "soft_failures": soft_counts,
        "unresolved_tasks": len(unresolved),
        "max_fix_loops_reached": total_fix_loops >= max_fix_loops_total,
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
    )

    return (
        final_ok and task_failures == 0,
        prd_struct,
        plan,
        last_validation,
    )
