from pathlib import Path

import pytest

from autodev.exec_kernel import ExecKernel


def test_is_command_available_checks_binary_presence(monkeypatch, tmp_path: Path):
    kernel = ExecKernel(cwd=str(tmp_path), timeout_sec=10)

    def fake_which(cmd):
        if cmd == "semgrep":
            return None
        if cmd == "python":
            return "/usr/bin/python"
        return "/usr/bin/docker"

    monkeypatch.setattr("autodev.exec_kernel.shutil.which", fake_which)
    assert kernel.is_command_available(["semgrep", "--config", ".semgrep.yml", "--error"]) is False
    assert kernel.is_command_available(["python", "-I", "-m", "ruff", "--version"]) is True


def test_is_command_available_blocks_disallowed_shape():
    kernel = ExecKernel(cwd=".", timeout_sec=10)
    assert kernel.is_command_available(["python", "-c", "print('x')"]) is False
    assert kernel.is_command_available(["docker", "run", "hello"]) is False


def test_run_rejects_disallowed_shape():
    kernel = ExecKernel(cwd=".", timeout_sec=10)
    with pytest.raises(RuntimeError):
        kernel.run(["python", "-c", "print('x')"])


def test_docker_build_is_blocked_for_unsafe_dockerfile(monkeypatch, tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\nADD . /app\n", encoding="utf-8")
    kernel = ExecKernel(cwd=str(tmp_path), timeout_sec=10)

    def fake_which(cmd):
        return f"/{cmd}"

    monkeypatch.setattr("autodev.exec_kernel.shutil.which", fake_which)
    assert kernel.is_command_available(["docker", "build", "."]) is False
    with pytest.raises(RuntimeError):
        kernel.run(["docker", "build", "."])


def test_docker_build_policy_can_be_disabled_with_constructor_flag(monkeypatch, tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11\nADD . /app\n", encoding="utf-8")
    kernel = ExecKernel(cwd=str(tmp_path), timeout_sec=10, strict_dockerfile_policy=False)

    def fake_which(cmd):
        return f"/{cmd}"

    monkeypatch.setattr("autodev.exec_kernel.shutil.which", fake_which)
    assert kernel.is_command_available(["docker", "build", "."]) is True
