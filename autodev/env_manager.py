from __future__ import annotations
import hashlib
import json
import os
from .exec_kernel import ExecKernel


class EnvManager:
    def __init__(self, kernel: ExecKernel):
        self.k = kernel

    def venv_python(self) -> str:
        win = os.name == "nt"
        return os.path.join(self.k.cwd, ".venv", "Scripts" if win else "bin", "python.exe" if win else "python")

    def _require_success(self, result, action: str) -> None:
        if result.returncode != 0:
            cmd = " ".join(result.cmd)
            raise RuntimeError(
                f"{action} failed (exit={result.returncode}).\n"
                f"command: {cmd}\n"
                f"stderr: {result.stderr.strip() or '<empty>'}\n"
                f"stdout: {result.stdout.strip() or '<empty>'}"
            )

    def ensure_venv(self, system_python: str = "python") -> None:
        py = self.venv_python()
        if os.path.exists(py):
            return
        self._require_success(self.k.run(self.k.module_cmd(system_python, "venv", ".venv")), "venv bootstrap")

    def _requirements_fingerprint(self, include_dev: bool) -> str:
        digest = hashlib.sha256()
        for rel in ["requirements.txt", "requirements-dev.txt"]:
            if rel == "requirements-dev.txt" and not include_dev:
                continue
            abs_path = os.path.join(self.k.cwd, rel)
            digest.update(rel.encode("utf-8"))
            if os.path.exists(abs_path):
                with open(abs_path, "rb") as fp:
                    digest.update(fp.read())
            else:
                digest.update(b"<missing>")
        return digest.hexdigest()

    def _bootstrap_stamp_path(self) -> str:
        return os.path.join(self.k.cwd, ".autodev", "env_bootstrap.json")

    def _is_bootstrap_current(self, *, include_dev: bool) -> bool:
        stamp_path = self._bootstrap_stamp_path()
        if not os.path.exists(stamp_path):
            return False
        try:
            with open(stamp_path, "r", encoding="utf-8") as fp:
                payload = json.loads(fp.read())
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        expected = {
            "venv_python": self.venv_python(),
            "include_dev": include_dev,
            "requirements_fingerprint": self._requirements_fingerprint(include_dev),
        }
        return payload == expected

    def _write_bootstrap_stamp(self, *, include_dev: bool) -> None:
        os.makedirs(os.path.join(self.k.cwd, ".autodev"), exist_ok=True)
        payload = {
            "venv_python": self.venv_python(),
            "include_dev": include_dev,
            "requirements_fingerprint": self._requirements_fingerprint(include_dev),
        }
        with open(self._bootstrap_stamp_path(), "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False)

    def install_requirements(self, include_dev: bool | None = None) -> None:
        if include_dev is None:
            include_dev = os.path.exists(os.path.join(self.k.cwd, "requirements-dev.txt"))

        py = self.venv_python()
        if os.path.exists(py) and self._is_bootstrap_current(include_dev=include_dev):
            return

        self._require_success(self.k.run(self.k.module_cmd(py, "pip", "install", "-U", "pip")), "pip bootstrap")
        self._require_success(self.k.run(self.k.module_cmd(py, "pip", "install", "-r", "requirements.txt")), "requirements bootstrap")
        if include_dev:
            dev_file = os.path.join(self.k.cwd, "requirements-dev.txt")
            if os.path.exists(dev_file):
                self._require_success(
                    self.k.run(self.k.module_cmd(py, "pip", "install", "-r", "requirements-dev.txt")),
                    "requirements-dev bootstrap",
                )

        self._write_bootstrap_stamp(include_dev=include_dev)
