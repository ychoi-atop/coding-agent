from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class CmdResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


class ExecKernel:
    """
    Security: allowlist only. No shell=True. No arbitrary commands.
    """

    ALLOWED_PY_MODULES = {"pytest", "ruff", "mypy", "pip_audit", "bandit"}
    ALLOWLIST_PREFIXES = [
        ["docker", "build"],
        ["docker", "version"],
    ]

    def __init__(self, cwd: str, timeout_sec: int = 600):
        self.cwd = cwd
        self.timeout = timeout_sec
        venv_python = os.path.join(cwd, ".venv", "bin", "python")
        if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
            self.python_executable = venv_python
        else:
            self.python_executable = shutil.which("python") or shutil.which("python3") or sys.executable

    def _allowed(self, cmd: list[str]) -> bool:
        if len(cmd) >= 4 and cmd[1] == "-I" and cmd[2] == "-m":
            exe = os.path.basename(cmd[0])
            if exe.startswith("python") and cmd[3] in self.ALLOWED_PY_MODULES:
                return True
        for prefix in self.ALLOWLIST_PREFIXES:
            if cmd[: len(prefix)] == prefix:
                return True
        return False

    def module_cmd(self, module: str, *args: str) -> list[str]:
        if module not in self.ALLOWED_PY_MODULES:
            raise ValueError(f"Module not allowlisted: {module}")
        # Use isolated mode to avoid local module shadowing (e.g., ruff.py in repo root).
        return [self.python_executable, "-I", "-m", module, *args]

    def run(self, cmd: list[str]) -> CmdResult:
        if not self._allowed(cmd):
            raise RuntimeError(f"Command not allowed: {cmd}")
        proc = subprocess.run(
            cmd,
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return CmdResult(
            cmd=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
