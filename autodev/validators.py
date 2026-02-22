from __future__ import annotations

from dataclasses import dataclass

from .exec_kernel import CmdResult, ExecKernel


@dataclass
class Validation:
    name: str
    ok: bool
    result: CmdResult


class Validators:
    def __init__(self, kernel: ExecKernel):
        self.kernel = kernel

    def run_all(self, enabled: list[str]) -> list[Validation]:
        out: list[Validation] = []
        for validator in enabled:
            out.append(self.run_one(validator))
        return out

    def run_one(self, name: str) -> Validation:
        if name == "ruff":
            result = self.kernel.run(self.kernel.module_cmd("ruff", "check", "."))
        elif name == "mypy":
            result = self.kernel.run(self.kernel.module_cmd("mypy", "."))
        elif name == "pytest":
            result = self.kernel.run(self.kernel.module_cmd("pytest", "-q"))
        elif name == "pip_audit":
            result = self.kernel.run(
                self.kernel.module_cmd(
                    "pip_audit",
                    "-r",
                    "requirements.txt",
                    "--cache-dir",
                    ".pip_audit_cache",
                )
            )
        elif name == "bandit":
            result = self.kernel.run(self.kernel.module_cmd("bandit", "-q", "-r", "src"))
        elif name == "docker_build":
            result = self.kernel.run(["docker", "build", "-t", "autodev-app:test", "."])
        else:
            raise ValueError(f"Unknown validator: {name}")
        return Validation(name=name, ok=(result.returncode == 0), result=result)
