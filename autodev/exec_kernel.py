from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass
class CmdResult:
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str


class ExecKernel:
    """Safe command runner. No shell=True. Enforces allowlist."""

    ALLOWED_PY_MODULES = {
        "ruff",
        "mypy",
        "pip_audit",
        "bandit",
        "pytest",
        "pip",
        "venv",
        "semgrep",
    }
    ALLOWED_PY_SCRIPTS = {"scripts/generate_sbom.py"}
    ALLOWED_DOCKER_CMDS = {"version", "build"}
    _DOCKERFILE_POLICY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("ADD instruction", re.compile(r"^\s*ADD\s+", re.IGNORECASE)),
        ("APT package-manager execution", re.compile(r"^\s*RUN\s+.*\bapt(\-get)?\b", re.IGNORECASE)),
        ("network tool execution", re.compile(r"^\s*RUN\s+.*\b(curl|wget|nc|ncat|ssh)\b", re.IGNORECASE)),
        ("root user declaration", re.compile(r"^\s*USER\s+root\b", re.IGNORECASE)),
    )
    _DOCKERFILE_POLICY_CONTEXT_DEFAULT = "."

    def __init__(self, cwd: str, timeout_sec: int = 1200, *, strict_dockerfile_policy: bool = True):
        self.cwd = cwd
        self.timeout = timeout_sec
        self._reject_reason: str | None = None
        self.strict_dockerfile_policy = strict_dockerfile_policy

    def _is_python(self, exe: str) -> bool:
        b = os.path.basename(exe).lower()
        return b.startswith("python")

    def module_cmd(self, python_executable: str, module: str, *args: str) -> List[str]:
        return [python_executable, "-I", "-m", module, *args]

    def script_cmd(self, python_executable: str, script_rel_path: str, *args: str) -> List[str]:
        return [python_executable, "-I", script_rel_path, *args]

    @staticmethod
    def _normalize_relpath(path: str) -> str:
        return path.replace("\\", "/").lstrip("./")

    def _normalize_cmd(self, cmd: List[str]) -> List[str]:
        return [str(c) for c in cmd]

    def is_command_available(self, cmd: List[str]) -> bool:
        if not cmd:
            return False
        normalized = self._normalize_cmd(cmd)
        if not self._allowed(normalized):
            return False

        base = normalized[0]
        if base in {"semgrep", "semgrep.exe"}:
            return shutil.which(base) is not None

        if self._looks_like_semgrep(normalized) and self._is_python(base):
            # python -I -m semgrep
            return True

        if base in {"docker", "docker.exe"}:
            return shutil.which(base) is not None

        if not self._is_python(base):
            return False

        if len(normalized) >= 4 and normalized[1] == "-I" and normalized[2] == "-m":
            # python module invocation; command exists if executable exists
            return shutil.which(base) is not None

        if len(normalized) >= 4 and normalized[1] == "-I":
            rel = self._normalize_relpath(normalized[2])
            return os.path.exists(os.path.join(self.cwd, rel))

        return False

    @staticmethod
    def _looks_like_semgrep(cmd: List[str]) -> bool:
        base = os.path.basename(cmd[0]).lower()
        if base in {"semgrep", "semgrep.exe"}:
            return True
        if base in {"python", "python.exe", "python3", "python3.exe"} and len(cmd) >= 4:
            return cmd[1:4] == ["-I", "-m", "semgrep"]
        return False

    @staticmethod
    def _looks_like_docker_build(cmd: List[str]) -> bool:
        return len(cmd) >= 2 and cmd[0] in {"docker", "docker.exe"} and cmd[1] == "build"

    @staticmethod
    def _is_option_with_argument(token: str) -> bool:
        return token in {
            "--file",
            "-f",
            "--tag",
            "-t",
            "--network",
            "--platform",
            "--build-arg",
            "--label",
            "--secret",
            "--cache-from",
            "--progress",
            "--volume",
            "--add-host",
        }

    @staticmethod
    def _extract_dockerfile_and_context(args: List[str]) -> tuple[str | None, str]:
        dockerfile: str | None = None
        context: str = ExecKernel._DOCKERFILE_POLICY_CONTEXT_DEFAULT

        i = 2
        while i < len(args):
            token = args[i]
            if token == "--file" or token == "-f":
                if i + 1 < len(args):
                    dockerfile = args[i + 1]
                    i += 2
                    continue
            elif token.startswith("--file="):
                dockerfile = token.split("=", 1)[1]
            elif token.startswith("--network"):
                # guard against host networking and skip network argument pair/value
                if token == "--network" and i + 1 < len(args):
                    i += 2
                    continue
            elif token.startswith("--network="):
                pass

            if token.startswith("-"):
                if ExecKernel._is_option_with_argument(token):
                    i += 2
                    continue
                i += 1
                continue

            context = token
            i += 1

        return dockerfile, context

    def _scan_dockerfile_policy(self, dockerfile_path: str) -> tuple[bool, str | None]:
        if not self.strict_dockerfile_policy:
            return True, None
        if not os.path.exists(dockerfile_path):
            return False, f"Dockerfile not found: {dockerfile_path}"

        try:
            with open(dockerfile_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as exc:
            return False, f"Unable to read Dockerfile: {dockerfile_path}: {exc}"

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            for msg, pattern in self._DOCKERFILE_POLICY_PATTERNS:
                if pattern.search(line):
                    return False, f"{msg}: {line}"

        return True, None

    def _ensure_safe_docker_build(self, cmd: List[str]) -> bool:
        if not self.strict_dockerfile_policy:
            return True

        args = cmd[2:]
        if "--network=host" in args:
            self._reject_reason = "docker build with --network=host is blocked by policy"
            return False

        for i, token in enumerate(args):
            if token == "--network" and i + 1 < len(args) and args[i + 1] == "host":
                self._reject_reason = "docker build with --network host is blocked by policy"
                return False

        dockerfile_rel, context = self._extract_dockerfile_and_context(cmd)
        dockerfile = dockerfile_rel or "Dockerfile"
        dockerfile_path = dockerfile
        if not os.path.isabs(dockerfile_path):
            dockerfile_path = os.path.join(context, dockerfile)
            if not os.path.isabs(context) and context != self._DOCKERFILE_POLICY_CONTEXT_DEFAULT:
                dockerfile_path = os.path.join(self.cwd, context, dockerfile)
            else:
                dockerfile_path = os.path.join(self.cwd, dockerfile_path)

        allowed, reason = self._scan_dockerfile_policy(dockerfile_path)
        if not allowed:
            self._reject_reason = reason or "dockerfile violates policy"
            return False

        return True

    def _allowed(self, cmd: List[str]) -> bool:
        self._reject_reason = None
        if not cmd:
            self._reject_reason = "empty command"
            return False

        normalized = self._normalize_cmd(cmd)

        if self._looks_like_docker_build(normalized):
            if not self._ensure_safe_docker_build(normalized):
                if self._reject_reason:
                    return False
                self._reject_reason = f"docker build blocked by policy: {normalized}"
                return False

        if normalized[0] == "docker":
            if len(normalized) >= 2 and normalized[1] in self.ALLOWED_DOCKER_CMDS:
                return True
            self._reject_reason = f"docker command not permitted: {normalized}"
            return False

        if self._looks_like_semgrep(normalized):
            # Support direct binary call or `python -I -m semgrep`.
            if normalized[0] in {"semgrep", "semgrep.exe"}:
                semgrep_args = normalized[1:]
            else:
                if len(normalized) < 4 or normalized[1:4] != ["-I", "-m", "semgrep"]:
                    self._reject_reason = f"unsupported semgrep invocation: {normalized}"
                    return False
                semgrep_args = normalized[4:]

            if semgrep_args == ["--version"]:
                return True

            if "--config" not in semgrep_args or "--error" not in semgrep_args:
                self._reject_reason = f"semgrep command missing required flags: {normalized}"
                return False

            cfg_candidates = [idx for idx, v in enumerate(semgrep_args) if v == "--config"]
            if not cfg_candidates:
                self._reject_reason = f"semgrep command missing --config: {normalized}"
                return False

            cfg_index = cfg_candidates[0]
            cfg_path = semgrep_args[cfg_index + 1] if cfg_index + 1 < len(semgrep_args) else ""
            if cfg_path != ".semgrep.yml":
                self._reject_reason = f"semgrep command must use .semgrep.yml config: {normalized}"
                return False

            for i, arg in enumerate(semgrep_args):
                if arg.startswith("-"):
                    continue
                if i == cfg_index + 1:
                    continue
                self._reject_reason = f"semgrep invocation has unsupported positional argument: {arg}"
                return False

            if not semgrep_args:
                self._reject_reason = f"semgrep command malformed: {normalized}"
                return False

            return True

        if not self._is_python(normalized[0]):
            self._reject_reason = f"non-python command blocked: {normalized[0]}"
            return False
        if len(normalized) < 4 or normalized[1] != "-I":
            self._reject_reason = f"python command must include -I isolation flag: {normalized}"
            return False

        if normalized[2] == "-m":
            if len(normalized) < 4:
                self._reject_reason = f"python module invocation missing module name: {normalized}"
                return False
            mod = normalized[3]
            args = normalized[4:]
            if mod not in self.ALLOWED_PY_MODULES:
                self._reject_reason = f"python module blocked: {mod}"
                return False
            if mod == "pip":
                allowed = {
                    ("install", "-U", "pip"),
                    ("install", "-r", "requirements.txt"),
                    ("install", "--no-cache-dir", "-r", "requirements.txt"),
                    ("install", "-r", "requirements-dev.txt"),
                    ("install", "--no-cache-dir", "-r", "requirements-dev.txt"),
                }
                if tuple(args) not in allowed:
                    self._reject_reason = f"unsupported pip arguments: {args}"
                    return False
                return True
            if mod == "venv":
                return args == [".venv"]
            if mod == "semgrep":
                if "--version" in args and len(args) == 1:
                    return True
                self._reject_reason = f"unsupported python -m semgrep invocation: {normalized}"
                return False
            return True

        if len(normalized) >= 3 and normalized[2] not in {".", "./"}:
            rel = self._normalize_relpath(normalized[2])
            if rel not in self.ALLOWED_PY_SCRIPTS:
                self._reject_reason = f"script path blocked: {normalized[2]}"
                return False
            if len(normalized) != 3:
                self._reject_reason = f"script invocation must include only script path: {normalized}"
                return False
            return True

        self._reject_reason = f"unsupported command shape: {' '.join(normalized)}"
        return False

    def run(self, cmd: List[str]) -> CmdResult:
        if not self._allowed(cmd):
            reason = self._reject_reason or "unknown"
            raise RuntimeError(f"Command not allowed: {cmd}. reason={reason}")
        try:
            p = subprocess.run(
                cmd,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            return CmdResult(cmd=cmd, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
        except Exception as e:
            return CmdResult(cmd=cmd, returncode=127, stdout="", stderr=str(e))
