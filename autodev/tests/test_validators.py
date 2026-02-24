import pytest


from autodev.validators import Validators, register_validator, get_validator_definition
from autodev import validators as validators_module
from autodev.exec_kernel import ExecKernel, CmdResult


class _FakeKernel:
    def __init__(self, command_results=None, available=True):
        self.command_results = dict(command_results or {})
        self._available = available
        self.cwd = "."

    def run(self, cmd):
        cmd_key = tuple(cmd)
        if cmd_key in self.command_results:
            code = self.command_results[cmd_key]
        elif "pip_audit" in cmd:
            code = self.command_results.get("pip_audit", 0)
        elif "ruff" in cmd:
            code = self.command_results.get("ruff", 0)
        elif "semgrep" in cmd:
            code = self.command_results.get("semgrep", 0)
        else:
            code = 0
        return CmdResult(cmd=cmd, returncode=code, stdout="", stderr="")

    def is_command_available(self, cmd):
        return bool(self._available)

    def module_cmd(self, python_executable, module, *args):
        return [python_executable, "-I", "-m", module, *args]

    def script_cmd(self, python_executable, script_rel_path, *args):
        return [python_executable, "-I", script_rel_path, *args]


class _FakeEnvManager:
    def __init__(self, kernel: _FakeKernel):
        self._kernel = kernel

    def venv_python(self) -> str:
        return "/fake/python"


def test_validator_soft_and_hard_classification_and_serialize_fields():
    kernel = _FakeKernel({"pip_audit": 1, "semgrep": 1})
    validators = Validators(kernel=kernel, env=_FakeEnvManager(kernel))
    results = validators.run_all(["pip_audit", "ruff"], audit_required=False, soft_validators={"pip_audit"}, phase="per_task")

    payload = Validators.serialize(results)
    assert payload[0]["name"] == "ruff"
    assert payload[1]["name"] == "pip_audit"
    assert payload[1]["status"] == "soft_fail"
    assert "status" in payload[1]
    assert "tool_version" in payload[0]
    assert "duration_ms" in payload[0]

    hard = validators.run_all(["semgrep"], audit_required=True, soft_validators=set(), phase="final")
    hard_payload = Validators.serialize(hard)
    assert hard_payload[0]["status"] in {"passed", "failed", "soft_fail"}
    assert hard_payload[0]["phase"] == "final"


def test_exec_kernel_rejects_unsupported_command_shapes():
    kernel = ExecKernel(cwd=".", timeout_sec=10)

    with pytest.raises(RuntimeError):
        kernel.run(["python", "-c", "print('x')"])

    with pytest.raises(RuntimeError):
        kernel.run(["semgrep", "--config", "other.yml"])

    with pytest.raises(RuntimeError):
        kernel.run(["python", "-I", "-m", "semgrep", "scan", "--config", ".semgrep.yml", "--error"])


def test_semgrep_variants_allowed_with_strict_shape():
    kernel = ExecKernel(cwd=".", timeout_sec=10)

    assert kernel._allowed(["semgrep", "--config", ".semgrep.yml", "--error"]) is True
    assert kernel._allowed(["python", "-I", "-m", "semgrep", "--config", ".semgrep.yml", "--error"]) is True


def test_run_all_preserves_phase_and_soft_split():
    fake_kernel = _FakeKernel()
    validators = Validators(kernel=fake_kernel, env=_FakeEnvManager(fake_kernel))
    results = validators.run_all(["ruff", "pip_audit", "semgrep"], soft_validators={"pip_audit"}, phase="per_task")
    payload = Validators.serialize(results)

    assert payload[0]["phase"] == "per_task"
    assert {r["name"] for r in payload} == {"ruff", "pip_audit", "semgrep"}
    assert payload[1]["name"] == "semgrep"
    assert payload[2]["name"] == "pip_audit"
    assert payload[2]["phase"] == "per_task"


def test_unavailable_tool_is_reported_as_failed_without_throwing():
    fake_kernel = _FakeKernel(available=False)
    validators = Validators(kernel=fake_kernel, env=_FakeEnvManager(fake_kernel))

    result = validators.run_one("docker_build", phase="final")
    assert result.status == "failed"
    assert result.ok is False
    assert result.error_classification == "tool_unavailable"
    assert result.note == "validator command unavailable"
    assert result.result.returncode == 127


def test_disallowed_command_exception_maps_to_failed_validation():
    class _RejectingKernel(_FakeKernel):
        def run(self, cmd):
            raise RuntimeError("Command not allowed")

    kernel = _RejectingKernel()
    validators = Validators(kernel=kernel, env=_FakeEnvManager(kernel))

    result = validators.run_one("ruff")
    assert result.status == "failed"
    assert result.ok is False
    assert result.error_classification == "tool_error"


def test_validator_result_events_include_context(monkeypatch):
    kernel = _FakeKernel()
    validators = Validators(kernel=kernel, env=_FakeEnvManager(kernel))
    events: list[dict[str, object]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    import autodev.validators as vmod

    monkeypatch.setattr(vmod, "_log_event", _capture)

    validators.run_one("ruff", run_id="run-log", request_id="req-log", profile="minimal", task_id="task-1", iteration=2)
    validators._version = lambda _v, python_executable="python": "1.2.3"

    assert any(evt.get("event") == "validator.result" for evt in events)
    evt = next(evt for evt in events if evt.get("event") == "validator.result")
    assert evt.get("run_id") == "run-log"
    assert evt.get("request_id") == "req-log"
    assert evt.get("task_id") == "task-1"
    assert evt.get("iteration") == 2
    assert evt.get("validator") == "ruff"


def test_run_one_uses_registered_validator_lookup(monkeypatch):
    fake_kernel = _FakeKernel(command_results={"/fake/custom": 0, "/fake/custom-version": 0}, available=True)

    register_validator(
        "custom_check",
        lambda py: ["/fake/custom"],
        version_builder=lambda py: ["/fake/custom-version"],
    )
    validator = Validators(kernel=fake_kernel, env=_FakeEnvManager(fake_kernel))

    try:
        result = validator.run_one("custom_check")
    finally:
        validators_module._VALIDATOR_REGISTRY.pop("custom_check", None)

    assert result.name == "custom_check"
    assert result.result.cmd == ["/fake/custom"]


def test_run_one_raises_for_unknown_validator():
    kernel = _FakeKernel()
    validators = Validators(kernel=kernel, env=_FakeEnvManager(kernel))

    with pytest.raises(ValueError, match="Unknown validator: missing_validator"):
        validators.run_one("missing_validator")


def test_dependency_lock_validator_flags_unpinned_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    kernel = _FakeKernel()
    kernel.cwd = str(tmp_path)
    validator = Validators(kernel=kernel, env=_FakeEnvManager(kernel))

    result = validator.run_one("dependency_lock")

    assert result.name == "dependency_lock"
    assert result.ok is False
    assert result.status == "failed"
    assert result.error_classification == "policy_violation"
    assert "unpinned" in result.result.stdout


def test_dependency_lock_validator_allows_pinned_requirements_with_lock_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.111.0\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("{}", encoding="utf-8")
    kernel = _FakeKernel()
    kernel.cwd = str(tmp_path)
    validator = Validators(kernel=kernel, env=_FakeEnvManager(kernel))

    result = validator.run_one("dependency_lock")

    assert result.ok is True
    assert result.status == "passed"
    assert result.error_classification is None


def test_configured_default_validator_names_match_registry():
    assert get_validator_definition("ruff") is not None
    from autodev.schemas import VALIDATORS

    assert set(VALIDATORS) == set(["ruff", "mypy", "pytest", "pip_audit", "bandit", "semgrep", "sbom", "docker_build", "dependency_lock"])
