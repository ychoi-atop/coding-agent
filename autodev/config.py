from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import yaml  # type: ignore[import-untyped]

from .schemas import VALIDATORS

_POLICY_SECTIONS = {"per_task", "final"}
_POLICY_KEYS = {"soft_fail"}
_QUALITY_PROFILE_KEYS = {
    "name",
    "validator_policy",
    "per_task_soft",
    "final_soft",
    "by_level",
    "escalation",
}
_PROFILE_REQUIRED_KEYS = {"validators", "template_candidates"}
_PROFILE_OPTIONAL_KEYS = {
    "security",
    "validator_policy",
    "quality_profile",
    "disable_docker_build",
}
_PROFILE_ALLOWED_KEYS = _PROFILE_REQUIRED_KEYS | _PROFILE_OPTIONAL_KEYS
_AUTODEV_API_KEY_ENV = "AUTODEV_LLM_API_KEY"
_AUTODEV_API_KEY_PLACEHOLDER = f"${{{_AUTODEV_API_KEY_ENV}}}"
_DEFAULT_PROFILE_QUALITY_PROFILE: Dict[str, Any] = {"validator_policy": {"per_task": {}, "final": {}}}


def _resolve_api_key(value: Any) -> Any:
    if isinstance(value, str) and value == _AUTODEV_API_KEY_PLACEHOLDER:
        return os.getenv(_AUTODEV_API_KEY_ENV)
    return value


def _fmt_path(path_parts: List[str]) -> str:
    if not path_parts:
        return "<root>"
    return ".".join(path_parts)


def _coerce_int(value: Any, path: str, errors: List[str], default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        errors.append(f"{path} must be an integer, not a boolean.")
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        if value.strip() == "":
            errors.append(f"{path} must be an integer, got empty string.")
            return default
        try:
            return int(value)
        except ValueError:
            pass
    errors.append(f"{path} must be an integer, got {type(value).__name__}.")
    return default


def _validate_string_list(value: Any, path_parts: List[str], errors: List[str]) -> List[str]:
    if not isinstance(value, list):
        errors.append(f"{_fmt_path(path_parts)} must be a list of strings.")
        return []
    out: List[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{_fmt_path(path_parts + [f'[{i}]'])} must be a string.")
            continue
        out.append(item)
    return out


def _validate_required_profile_fields(profile_name: str, profile: dict[str, Any], errors: List[str]) -> None:
    for key in sorted(_PROFILE_REQUIRED_KEYS):
        if key not in profile:
            errors.append(f"profiles.{profile_name}.{key} is required.")


def _validate_profile_keys(profile_name: str, profile: dict[str, Any], errors: List[str]) -> None:
    unknown = sorted(set(profile.keys()) - _PROFILE_ALLOWED_KEYS)
    if unknown:
        errors.append(
            f"profiles.{profile_name} has unknown key(s): {unknown}. "
            f"Allowed keys: {sorted(_PROFILE_ALLOWED_KEYS)}."
        )


def _normalize_profile_defaults(profile_name: str, profile: dict[str, Any], errors: List[str]) -> None:
    security = profile.get("security")
    if security is None:
        profile["security"] = {"audit_required": False}
    elif not isinstance(security, dict):
        errors.append(f"profiles.{profile_name}.security must be an object.")
    elif security.get("audit_required") is None:
        security["audit_required"] = False
    elif not isinstance(security.get("audit_required"), bool):
        errors.append(f"profiles.{profile_name}.security.audit_required must be a boolean.")

    disable_docker_build = profile.get("disable_docker_build")
    if disable_docker_build is None:
        profile["disable_docker_build"] = False
    elif not isinstance(disable_docker_build, bool):
        errors.append(f"profiles.{profile_name}.disable_docker_build must be a boolean.")

    quality_profile = profile.get("quality_profile")
    if quality_profile is None:
        profile["quality_profile"] = dict(_DEFAULT_PROFILE_QUALITY_PROFILE)
        return
    if not isinstance(quality_profile, dict):
        errors.append(f"profiles.{profile_name}.quality_profile must be an object.")
        return

    legacy_policy = profile.get("validator_policy")
    if legacy_policy is None:
        return
    if not isinstance(legacy_policy, dict):
        errors.append(f"profiles.{profile_name}.validator_policy must be an object.")
        return
    legacy_serialized = json.dumps(legacy_policy, sort_keys=True)
    new_policy = quality_profile.get("validator_policy")
    if new_policy is None:
        quality_profile["validator_policy"] = legacy_policy
        return
    if not isinstance(new_policy, dict):
        return
    if json.dumps(new_policy, sort_keys=True) != legacy_serialized:
        errors.append(
            f"profiles.{profile_name} has ambiguous policy configuration: "
            "profiles.validator_policy and quality_profile.validator_policy differ. "
            "Configure policy only in quality_profile.validator_policy."
        )


def _validate_profile_types(profile_name: str, profile: dict[str, Any], errors: List[str]) -> None:
    validators = _validate_string_list(
        profile.get("validators"),
        ["profiles", profile_name, "validators"],
        errors,
    )
    if validators == []:
        errors.append(f"profiles.{profile_name}.validators must be a non-empty list of strings.")
    else:
        for i, name in enumerate(validators):
            item_path = _fmt_path(["profiles", profile_name, f"validators[{i}]"])
            if name not in VALIDATORS:
                errors.append(
                    f"{item_path} has unknown validator '{name}'. "
                    f"Allowed validators: {VALIDATORS}."
                )

    template_candidates = profile.get("template_candidates")
    if template_candidates is None:
        return
    candidates = _validate_string_list(template_candidates, ["profiles", profile_name, "template_candidates"], errors)
    if template_candidates is not None and candidates == []:
        errors.append(f"profiles.{profile_name}.template_candidates must be a non-empty list of strings.")


def _validate_validator_policy(
    profile_name: str,
    policy: Any,
    errors: List[str],
    path_prefix: List[str] | None = None,
) -> None:
    base = path_prefix or ["profiles", profile_name, "validator_policy"]
    if policy is None:
        return
    if not isinstance(policy, dict):
        errors.append(f"{_fmt_path(base)} must be an object.")
        return

    unknown_sections = sorted(set(policy.keys()) - _POLICY_SECTIONS)
    if unknown_sections:
        errors.append(
            f"{_fmt_path(base)} has unknown section(s): {unknown_sections}. "
            f"Allowed sections: {sorted(_POLICY_SECTIONS)}."
        )

    known_set = set(VALIDATORS)
    for section in sorted(_POLICY_SECTIONS):
        if section not in policy:
            continue
        section_value = policy[section]
        section_path = base + [section]
        if not isinstance(section_value, dict):
            errors.append(f"{_fmt_path(section_path)} must be an object.")
            continue
        unknown_keys = sorted(set(section_value.keys()) - _POLICY_KEYS)
        if unknown_keys:
            errors.append(
                f"{_fmt_path(section_path)} has unknown key(s): {unknown_keys}. "
                f"Allowed keys: {sorted(_POLICY_KEYS)}."
            )

        if "soft_fail" not in section_value:
            continue
        soft_fail = _validate_string_list(section_value["soft_fail"], section_path + ["soft_fail"], errors)
        for i, name in enumerate(soft_fail):
            item_path = _fmt_path(section_path + ["soft_fail", f"[{i}]"])
            if name not in known_set:
                errors.append(
                    f"{item_path} has unknown validator '{name}'. "
                    f"Allowed validators: {VALIDATORS}."
                )


def _validate_profile_soft_lists(
    quality_obj: Any,
    errors: List[str],
    base: List[str],
) -> None:
    """Validate compact quality profile fields (per_task_soft, final_soft)."""
    known_set = set(VALIDATORS)
    if not isinstance(quality_obj, dict):
        return
    for key in ("per_task_soft", "final_soft"):
        if key not in quality_obj:
            continue
        values = _validate_string_list(quality_obj[key], base + [key], errors)
        for i, name in enumerate(values):
            if name not in known_set:
                errors.append(
                    f"{_fmt_path(base + [key, f'[{i}]'])} has unknown validator '{name}'. "
                    f"Allowed validators: {VALIDATORS}."
                )


def _validate_by_level_profiles(
    profile_name: str,
    by_level: Any,
    errors: List[str],
    path_prefix: List[str],
) -> None:
    if by_level is None:
        return
    if not isinstance(by_level, dict):
        errors.append(f"{_fmt_path(path_prefix)} must be an object.")
        return

    for level_name, level_config in by_level.items():
        level_path = path_prefix + [str(level_name)]
        if not isinstance(level_config, dict):
            errors.append(f"{_fmt_path(level_path)} must be an object.")
            continue

        unknown_level_keys = sorted(set(level_config.keys()) - _QUALITY_PROFILE_KEYS)
        if unknown_level_keys:
            errors.append(
                f"{_fmt_path(level_path)} has unknown key(s): {unknown_level_keys}. "
                f"Allowed keys: {sorted(_QUALITY_PROFILE_KEYS)}."
            )

        _validate_profile_soft_lists(level_config, errors, level_path)
        _validate_validator_policy(
            profile_name=f"{profile_name}:{level_name}",
            policy=level_config.get("validator_policy"),
            errors=errors,
            path_prefix=level_path + ["validator_policy"],
        )

        level_esc = level_config.get("escalation")
        if level_esc is None:
            continue
        if not isinstance(level_esc, dict):
            errors.append(f"{_fmt_path(level_path + ['escalation'])} must be an object.")
            continue

        unknown = sorted(set(level_esc.keys()) - {"repeat_failure_guard"})
        if unknown:
            errors.append(
                f"{_fmt_path(level_path + ['escalation'])} has unknown key(s): {unknown}. "
                "Allowed keys: ['repeat_failure_guard']."
            )

        repeat_guard = level_esc.get("repeat_failure_guard")
        if repeat_guard is None:
            continue
        if not isinstance(repeat_guard, dict):
            errors.append(f"{_fmt_path(level_path + ['escalation', 'repeat_failure_guard'])} must be an object.")
            continue

        enabled = repeat_guard.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(
                f"{_fmt_path(level_path + ['escalation', 'repeat_failure_guard', 'enabled'])} must be a boolean."
            )

        repeats = repeat_guard.get("max_retries_before_targeted_fix")
        if repeats is not None:
            if not isinstance(repeats, int) or repeats < 0:
                errors.append(
                    f"{_fmt_path(level_path + ['escalation', 'repeat_failure_guard', 'max_retries_before_targeted_fix'])} "
                    "must be a non-negative integer."
                )


def _validate_quality_profile(profile_name: str, quality_profile: Any, errors: List[str]) -> None:
    base = ["profiles", str(profile_name), "quality_profile"]
    if quality_profile is None:
        return
    if not isinstance(quality_profile, dict):
        errors.append(f"{_fmt_path(base)} must be an object.")
        return

    unknown_keys = sorted(set(quality_profile.keys()) - _QUALITY_PROFILE_KEYS)
    if unknown_keys:
        errors.append(
            f"{_fmt_path(base)} has unknown key(s): {unknown_keys}. "
            f"Allowed keys: {sorted(_QUALITY_PROFILE_KEYS)}."
        )

    name = quality_profile.get("name")
    if name is not None and name not in {"minimal", "balanced", "strict"}:
        errors.append(f"{_fmt_path(base + ['name'])} must be one of ['minimal', 'balanced', 'strict']")

    _validate_validator_policy(
        profile_name=profile_name,
        policy=quality_profile.get("validator_policy"),
        errors=errors,
        path_prefix=base + ["validator_policy"],
    )

    _validate_profile_soft_lists(quality_profile, errors, base)

    _validate_by_level_profiles(
        profile_name=str(profile_name),
        by_level=quality_profile.get("by_level"),
        errors=errors,
        path_prefix=base + ["by_level"],
    )

    escalation = quality_profile.get("escalation")
    if escalation is None:
        return
    if not isinstance(escalation, dict):
        errors.append(f"{_fmt_path(base + ['escalation'])} must be an object.")
        return

    unknown_guard_sections = sorted(set(escalation.keys()) - {"repeat_failure_guard"})
    if unknown_guard_sections:
        errors.append(
            f"{_fmt_path(base + ['escalation'])} has unknown key(s): {unknown_guard_sections}. "
            "Allowed keys: ['repeat_failure_guard']."
        )

    repeat_guard = escalation.get("repeat_failure_guard")
    if repeat_guard is None:
        return
    if not isinstance(repeat_guard, dict):
        errors.append(f"{_fmt_path(base + ['escalation', 'repeat_failure_guard'])} must be an object.")
        return

    enabled = repeat_guard.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append(f"{_fmt_path(base + ['escalation', 'repeat_failure_guard', 'enabled'])} must be a boolean.")

    repeats = repeat_guard.get("max_retries_before_targeted_fix")
    if repeats is not None:
        if not isinstance(repeats, int) or repeats < 0:
            errors.append(
                f"{_fmt_path(base + ['escalation', 'repeat_failure_guard', 'max_retries_before_targeted_fix'])} "
                "must be a non-negative integer."
            )


def _validate_llm_section(llm_cfg: Any, errors: List[str]) -> None:
    if not isinstance(llm_cfg, dict):
        errors.append("llm must be an object.")
        return

    base_url = llm_cfg.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        errors.append("llm.base_url is required and must be a non-empty string.")

    model = llm_cfg.get("model")
    if not isinstance(model, str) or not model.strip():
        errors.append("llm.model is required and must be a non-empty string.")

    resolved_api_key = _resolve_api_key(llm_cfg.get("api_key"))
    llm_cfg["api_key"] = resolved_api_key
    if not isinstance(resolved_api_key, str) or not resolved_api_key.strip():
        errors.append(
            "llm.api_key is required. Set llm.api_key in config.yaml or define AUTODEV_LLM_API_KEY in environment."
        )

    timeout = _coerce_int(llm_cfg.get("timeout_sec"), "llm.timeout_sec", errors, default=240)
    if timeout is not None:
        if timeout <= 0:
            errors.append("llm.timeout_sec must be a positive integer.")
        else:
            llm_cfg["timeout_sec"] = timeout


def _validate_profile_map(profiles: Any, errors: List[str]) -> None:
    if not isinstance(profiles, dict):
        errors.append("profiles must be an object mapping profile name to settings.")
        return
    if not profiles:
        errors.append("profiles must contain at least one profile.")
        return

    for profile_name, profile in profiles.items():
        profile_path = ["profiles", str(profile_name)]
        if not isinstance(profile, dict):
            errors.append(f"{_fmt_path(profile_path)} must be an object.")
            continue

        _validate_profile_keys(profile_name=str(profile_name), profile=profile, errors=errors)
        _validate_required_profile_fields(profile_name=str(profile_name), profile=profile, errors=errors)
        _validate_profile_types(profile_name=str(profile_name), profile=profile, errors=errors)
        _normalize_profile_defaults(profile_name=str(profile_name), profile=profile, errors=errors)
        _validate_quality_profile(
            profile_name=str(profile_name),
            quality_profile=profile.get("quality_profile"),
            errors=errors,
        )


def _validate_config(config: Any) -> Dict[str, Any]:
    errors: List[str] = []
    if not isinstance(config, dict):
        raise ValueError("Invalid config: top-level YAML must be an object.")

    llm_cfg = config.get("llm")
    if llm_cfg is None:
        errors.append("Missing required section: llm")
    else:
        _validate_llm_section(llm_cfg, errors)

    profiles = config.get("profiles")
    _validate_profile_map(profiles, errors)

    run = config.get("run")
    if run is None:
        config["run"] = {}
    if not isinstance(config["run"], dict):
        errors.append("run must be an object.")
        config["run"] = {}

    if errors:
        msg = "Invalid config:\n- " + "\n- ".join(errors)
        raise ValueError(msg)
    return config


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _validate_config(raw)
