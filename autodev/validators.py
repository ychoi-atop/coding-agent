from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .exec_kernel import CmdResult, ExecKernel
from .env_manager import EnvManager
from .json_utils import json_dumps

logger = logging.getLogger("autodev")

_LOCK_FILES = {
    "requirements.lock",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
}
_PINNED_REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(\[[^\]]+\])?==\S+$")


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


@dataclass
class Validation:
    name: str
    ok: bool
    result: CmdResult
    note: str = ""
    status: str = "done"
    duration_ms: int = 0
    tool_version: str = "unknown"
    error_classification: Optional[str] = None
    phase: str = "task"


CommandBuilder = Callable[[str], List[str]]


@dataclass(frozen=True)
class _ValidatorSpec:
    name: str
    command_builder: CommandBuilder
    version_builder: CommandBuilder


_VALIDATOR_REGISTRY: Dict[str, _ValidatorSpec] = {}
_DEFAULT_VALIDATOR_ORDER: List[str] = []


def _module_command(python_executable: str, module: str, *args: str) -> List[str]:
    return [python_executable, "-I", "-m", module, *args]


def _iter_requirements_lines(path: str, root: str, seen: set[str]):
    abs_path = os.path.join(root, path)
    if abs_path in seen:
        return
    if not os.path.exists(abs_path):
        return

    seen.add(abs_path)
    try:
        lines = open(abs_path, "r", encoding="utf-8").read().splitlines()
    except OSError:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("-r ") or line.startswith("--requirement "):
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                yield from _iter_requirements_lines(parts[1], root, seen)
            continue

        yield line


def _is_pinned_requirement(line: str) -> bool:
    cleaned = line.split(" #", 1)[0].strip()
    return bool(_PINNED_REQUIREMENT_RE.match(cleaned))


def _check_dependency_lock_policy(root: str) -> tuple[bool, str]:
    requirements_files = ["requirements.txt", "requirements-dev.txt"]
    violations: list[str] = []

    seen: set[str] = set()
    dep_lines: list[str] = []
    for req_file in requirements_files:
        dep_lines.extend(_iter_requirements_lines(req_file, root, seen))

    if not dep_lines:
        return True, ""

    for item in dep_lines:
        if item.startswith("-r") or item.startswith("--requirement"):
            continue
        if item.startswith("-e") or item.startswith("--editable"):
            violations.append(f"editable/local requirement should be resolved in lockfile: {item}")
            continue
        if item.startswith(("http://", "https://", "git+", "svn+", "hg+", "bzr+") ):
            violations.append(f"URL/VCS requirement should be lock-managed: {item}")
            continue
        if _is_pinned_requirement(item):
            continue
        violations.append(f"unpinned or unsupported requirement specifier: {item}")

    has_lock_file = any(os.path.exists(os.path.join(root, f)) for f in _LOCK_FILES)
    if not has_lock_file:
        violations.append(
            "dependency lock file missing; add one of: requirements.lock, poetry.lock, Pipfile.lock, uv.lock"
        )

    if violations:
        return False, "\n".join(violations)
    return True, ""


def register_validator(
    name: str,
    command_builder: CommandBuilder,
    *,
    version_builder: CommandBuilder,
    aliases: Sequence[str] = (),
    is_default: bool = False,
) -> _ValidatorSpec:
    spec = _ValidatorSpec(
        name=name,
        command_builder=command_builder,
        version_builder=version_builder,
    )
    _VALIDATOR_REGISTRY[name] = spec
    for alias in aliases:
        _VALIDATOR_REGISTRY[alias] = spec
    if is_default and name not in _DEFAULT_VALIDATOR_ORDER:
        _DEFAULT_VALIDATOR_ORDER.append(name)
    return spec


def get_validator_definition(name: str) -> Optional[_ValidatorSpec]:
    return _VALIDATOR_REGISTRY.get(name)


def registered_validator_names() -> Tuple[str, ...]:
    return tuple(_DEFAULT_VALIDATOR_ORDER)


def _register_default_validators() -> None:
    register_validator(
        "ruff",
        lambda py: _module_command(
            py,
            "ruff",
            "check",
            "src",
            "tests",
            "--select",
            "E,F,I,B,UP,SIM,S,ASYNC,PERF",
            "--line-length",
            "100",
        ),
        version_builder=lambda py: _module_command(py, "ruff", "--version"),
        is_default=True,
    )
    register_validator(
        "mypy",
        lambda py: _module_command(
            py,
            "mypy",
            "--hide-error-context",
            "--show-error-codes",
            "--pretty",
            "--install-types",
            "--non-interactive",
            "src",
        ),
        version_builder=lambda py: _module_command(py, "mypy", "--version"),
        is_default=True,
    )
    register_validator(
        "pytest",
        lambda py: _module_command(py, "pytest", "-q", "--maxfail", "1", "tests"),
        version_builder=lambda py: _module_command(py, "pytest", "--version"),
        is_default=True,
    )
    register_validator(
        "pip_audit",
        lambda py: _module_command(py, "pip_audit", "-r", "requirements.txt", "--format", "json"),
        version_builder=lambda py: _module_command(py, "pip_audit", "--version"),
        is_default=True,
    )
    register_validator(
        "bandit",
        lambda py: _module_command(py, "bandit", "-q", "-r", "src"),
        version_builder=lambda py: _module_command(py, "bandit", "--version"),
        is_default=True,
    )
    register_validator(
        "semgrep",
        lambda _py: ["semgrep", "--config", ".semgrep.yml", "--error"],
        version_builder=lambda _py: ["semgrep", "--version"],
        is_default=True,
    )
    register_validator(
        "sbom",
        lambda py: [py, "-I", "scripts/generate_sbom.py"],
        version_builder=lambda py: [py, "-I", "scripts/generate_sbom.py"],
        is_default=True,
    )
    register_validator(
        "docker_build",
        lambda _py: ["docker", "build", "--pull", "-t", "autodev-app:test", "."],
        version_builder=lambda _py: ["docker", "version"],
        is_default=True,
    )
    register_validator(
        "dependency_lock",
        lambda py: [py, "-I", "scripts/check_dependency_lock.py"],
        version_builder=lambda py: [py, "-I", "scripts/generate_sbom.py"],
        is_default=True,
    )


_register_default_validators()

# Shared default validator list stable and imported by schema/config validation.
DEFAULT_VALIDATOR_NAMES = registered_validator_names()


class Validators:
    def __init__(self, kernel: ExecKernel, env: EnvManager):
        self.k = kernel
        self.env = env
        self._version_cache: Dict[str, str] = {}

    @staticmethod
    def _looks_like_missing_tool(returncode: int, text: str) -> bool:
        if returncode == 127:
            return True
        lowered = text.lower()
        return any(token in lowered for token in [
            "command not found",
            "no such file or directory",
            "not recognized",
            "no module named",
        ])

    @staticmethod
    def _error_text(result: CmdResult) -> str:
        return f"{result.stdout}\n{result.stderr}".lower()

    def _parse_error_class(
        self,
        name: str,
        returncode: int,
        audit_required: bool,
        r: CmdResult,
    ) -> tuple[str, Optional[str]]:
        if returncode == 0:
            return "passed", None

        if self._looks_like_missing_tool(returncode, self._error_text(r)):
            return "failed", "tool_unavailable"

        if name == "pip_audit" and not audit_required:
            return "soft_fail", "warning_offline_or_vulnerable"

        if name in {"semgrep", "dependency_lock"}:
            return "failed", "policy_violation"

        return "failed", "tool_error"

    def _run(self, name: str, command: List[str], audit_required: bool = False, phase: str = "task", *, python_executable: str, run_id: str | None = None, request_id: str | None = None, profile: str | None = None, task_id: str | None = None, iteration: int | None = None, retry_count: int = 1,) -> Validation:
        start = time.perf_counter()
        try:
            result = self.k.run(command)
        except RuntimeError as exc:
            result = CmdResult(cmd=command, returncode=1, stdout="", stderr=str(exc))
        duration_ms = int((time.perf_counter() - start) * 1000)

        status, error_class = self._parse_error_class(name, result.returncode, audit_required, result)
        ok = status == "passed"
        note = ""
        if name == "pip_audit" and (result.returncode != 0) and (not audit_required):
            if error_class == "tool_unavailable":
                note = "pip-audit could not run in this environment. WARN because audit_required=false."
            else:
                note = "pip-audit failed (possibly offline). WARN because audit_required=false."

        validation = Validation(
            name=name,
            ok=ok,
            result=result,
            note=note,
            status=status,
            duration_ms=duration_ms,
            tool_version=self._version(name, python_executable=python_executable),
            error_classification=error_class,
            phase=phase,
        )

        _log_event(
            "validator.result",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id=task_id,
            iteration=iteration,
            phase=phase,
            validator=name,
            retry_count=retry_count,
            status=status,
            returncode=result.returncode,
            duration_ms=duration_ms,
            ok=ok,
        )

        return validation

    @staticmethod
    def _extract_version(output: str) -> str:
        if not output:
            return "unknown"
        m = re.search(r"\d+\.\d+(?:\.\d+)?", output)
        return m.group(0) if m else "unknown"

    def _version(self, validator: str, python_executable: str) -> str:
        if validator in self._version_cache:
            return self._version_cache[validator]

        spec = get_validator_definition(validator)
        if spec is None:
            self._version_cache[validator] = "unknown"
            return "unknown"

        try:
            probe = spec.version_builder(python_executable)
            r = self.k.run(probe)
            text = f"{r.stdout}\n{r.stderr}".strip()
            self._version_cache[validator] = self._extract_version(text)
        except Exception:
            self._version_cache[validator] = "unknown"
        return self._version_cache[validator]

    def _run_dependency_lock(
        self,
        python_executable: str,
        *,
        phase: str,
        run_id: str | None = None,
        request_id: str | None = None,
        profile: str | None = None,
        task_id: str | None = None,
        iteration: int | None = None,
        retry_count: int = 1,
    ) -> Validation:
        start = time.perf_counter()
        command = [python_executable, "-I", "scripts/check_dependency_lock.py"]
        ok, reason = _check_dependency_lock_policy(self.k.cwd)
        status = "passed" if ok else "failed"
        error_class = None if ok else "policy_violation"
        result = CmdResult(
            cmd=command,
            returncode=0 if ok else 1,
            stdout=reason or "",
            stderr="" if ok else "dependency lock policy violation",
        )

        duration_ms = int((time.perf_counter() - start) * 1000)
        validation = Validation(
            name="dependency_lock",
            ok=ok,
            result=result,
            note="" if ok else "Dependency lock policy check failed",
            status=status,
            duration_ms=duration_ms,
            tool_version="n/a",
            error_classification=error_class,
            phase=phase,
        )

        _log_event(
            "validator.result",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id=task_id,
            iteration=iteration,
            phase=phase,
            validator="dependency_lock",
            retry_count=retry_count,
            status=status,
            returncode=result.returncode,
            duration_ms=duration_ms,
            ok=ok,
        )

        return validation

    @staticmethod
    def _split_soft(validators_enabled: List[str], soft_validators: Set[str] | None = None) -> tuple[List[str], List[str]]:
        soft_set = set(soft_validators or [])
        hard: List[str] = []
        soft: List[str] = []
        for v in validators_enabled:
            if v in soft_set:
                soft.append(v)
            else:
                hard.append(v)

        return hard, soft

    def _unavailable_result(
        self,
        name: str,
        command: List[str],
        phase: str,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        profile: str | None = None,
        task_id: str | None = None,
        iteration: int | None = None,
        retry_count: int = 1,
    ) -> Validation:
        validation = Validation(
            name=name,
            ok=False,
            result=CmdResult(cmd=command, returncode=127, stdout="", stderr="tool command unavailable in environment"),
            note="validator command unavailable",
            status="failed",
            duration_ms=0,
            tool_version="unknown",
            error_classification="tool_unavailable",
            phase=phase,
        )

        _log_event(
            "validator.unavailable",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id=task_id,
            iteration=iteration,
            phase=phase,
            validator=name,
            retry_count=retry_count,
            error_classification="tool_unavailable",
        )
        return validation

    def run_all(
        self,
        enabled: List[str],
        audit_required: bool = False,
        soft_validators: Set[str] | None = None,
        phase: str = "task",
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        profile: str | None = None,
        task_id: str | None = None,
        iteration: int | None = None,
    ) -> List[Validation]:
        out: List[Validation] = []
        py = self.env.venv_python()

        hard, soft = self._split_soft(enabled, soft_validators=soft_validators)
        for name in hard + soft:
            out.append(
                self.run_one(
                    name,
                    audit_required=audit_required,
                    phase=phase,
                    preflight_python=py,
                    run_id=run_id,
                    request_id=request_id,
                    profile=profile,
                    task_id=task_id,
                    iteration=iteration,
                )
            )

        _log_event(
            "validator.phase_summary",
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id=task_id,
            iteration=iteration,
            phase=phase,
            hard_count=len(hard),
            soft_count=len(soft),
            total_run=len(out),
            validators=enabled,
        )

        return out

    def run_one(
        self,
        name: str,
        audit_required: bool = False,
        phase: str = "task",
        preflight_python: str | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        profile: str | None = None,
        task_id: str | None = None,
        iteration: int | None = None,
        retry_count: int = 1,
    ) -> Validation:
        py = preflight_python or self.env.venv_python()
        spec = get_validator_definition(name)

        if spec is None:
            raise ValueError(f"Unknown validator: {name}")

        if name == "dependency_lock":
            return self._run_dependency_lock(
                python_executable=py,
                phase=phase,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                task_id=task_id,
                iteration=iteration,
                retry_count=retry_count,
            )

        command = spec.command_builder(py)
        if not self.k.is_command_available(command):
            return self._unavailable_result(
                name=spec.name,
                command=command,
                phase=phase,
                run_id=run_id,
                request_id=request_id,
                profile=profile,
                task_id=task_id,
                iteration=iteration,
                retry_count=retry_count,
            )

        return self._run(
            name=spec.name,
            command=command,
            audit_required=audit_required,
            phase=phase,
            python_executable=py,
            run_id=run_id,
            request_id=request_id,
            profile=profile,
            task_id=task_id,
            iteration=iteration,
            retry_count=retry_count,
        )

    @staticmethod
    def serialize(results: List[Validation]) -> List[Dict[str, object]]:
        return [
            {
                "name": result.name,
                "ok": result.ok,
                "status": result.status,
                "phase": result.phase,
                "cmd": result.result.cmd,
                "returncode": result.result.returncode,
                "duration_ms": result.duration_ms,
                "tool_version": result.tool_version,
                "error_classification": result.error_classification,
                "stdout": result.result.stdout[-6000:],
                "stderr": result.result.stderr[-6000:],
                "note": result.note,
            }
            for result in results
        ]
