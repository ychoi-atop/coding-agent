from pathlib import Path
import sys
import json
import asyncio
from typing import Any, Optional, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from autodev.loop import _toposort, _resolve_validators, _validations_ok, _build_quality_row, _build_pass_map  # noqa: E402
from autodev.loop import _llm_json, run_autodev_enterprise  # noqa: E402
from autodev.workspace import Workspace  # noqa: E402
from autodev.report import write_report  # noqa: E402
from autodev.exec_kernel import ExecKernel, CmdResult  # noqa: E402
from autodev.env_manager import EnvManager  # noqa: E402
from autodev.llm_client import LLMClient  # noqa: E402


def test_toposort_orders_by_dependencies():
    tasks = [
        {"id": "compile", "depends_on": []},
        {"id": "test", "depends_on": ["compile"]},
        {"id": "lint", "depends_on": ["compile"]},
    ]

    ordered = _toposort(tasks)

    assert [t["id"] for t in ordered][:1] == ["compile"]
    assert set(t["id"] for t in ordered) == {"compile", "test", "lint"}
    assert ordered[-1]["id"] in {"test", "lint"}


def test_toposort_detects_cycle_with_actionable_error():
    tasks = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["c"]},
        {"id": "c", "depends_on": ["a"]},
    ]

    try:
        _toposort(tasks)
    except ValueError as e:
        msg = str(e)
        assert "Dependency cycle detected" in msg
        for t in ["a", "b", "c"]:
            assert f"{t}" in msg
    else:
        assert False, "Expected cycle detection to fail"


def test_resolve_validators_is_deterministic_and_intersects_enabled():
    assert _resolve_validators(["pytest", "missing", "ruff", "mypy"], ["ruff", "mypy", "pytest"]) == ["pytest", "ruff", "mypy"]


def test_validations_ok_respects_soft_and_hard_rules():
    rows = [
        {"name": "ruff", "ok": False, "status": "failed", "returncode": 1},
        {"name": "semgrep", "ok": False, "status": "soft_fail", "returncode": 1},
    ]

    assert _validations_ok(rows, {"semgrep"}) is False
    assert _validations_ok(rows, {"semgrep", "ruff"}) is True


def test_quality_row_and_pass_map_builder():
    rows = [
        {"name": "ruff", "ok": True, "status": "passed", "returncode": 0},
        {"name": "semgrep", "ok": False, "status": "soft_fail", "returncode": 1},
    ]
    row = _build_quality_row(
        task_id="task-1",
        attempt=1,
        run_set=["ruff", "semgrep"],
        validation_rows=rows,
        duration_ms=10,
        soft_validators={"semgrep"},
        all_ok=True,
    )

    assert row["hard_failures"] == 0
    assert row["soft_failures"] == 1
    pass_map = _build_pass_map(rows)
    assert pass_map["ruff"]["ok"] is True
    assert pass_map["semgrep"]["status"] == "soft_fail"


class _FakeResult:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _FakeValidation:
    def __init__(self, name: str, ok: bool, status: str, returncode: int):
        self.name = name
        self.ok = ok
        self.status = status
        self.result = _FakeResult(returncode)
        self.note = ""
        self.duration_ms = 0
        self.tool_version = "1.0"
        self.error_classification: Optional[str] = None


class _FakeValidators:
    calls: list[tuple[str, list[str], list[str], dict[str, Any]]] = []

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        out = []
        for r in results:
            out.append(
                {
                    "name": r.name,
                    "ok": r.ok,
                    "status": r.status,
                    "returncode": r.result.returncode,
                    "duration_ms": r.duration_ms,
                    "tool_version": r.tool_version,
                    "error_classification": r.error_classification,
                    "stdout": "",
                    "stderr": "",
                    "note": r.note,
                }
            )
        return out


    def run_one(self, name, audit_required=False, phase="task", **kwargs):
        return _FakeValidation(name, True, "passed", 0)

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        _FakeValidators.calls.append((phase, list(enabled), sorted(soft_validators or []), kwargs))

        if phase == "per_task":
            if len([c for c in _FakeValidators.calls if c[0] == "per_task"]) <= 2:
                return [_FakeValidation("ruff", False, "failed", 1)]
            return [_FakeValidation("ruff", True, "passed", 0)]

        return [_FakeValidation("ruff", True, "passed", 0)]


class _FakeKernel(ExecKernel):
    def __init__(self, cwd: str, timeout_sec: int = 1200):
        super().__init__(cwd=cwd, timeout_sec=timeout_sec)

    def run(self, cmd: list[str]) -> CmdResult:
        return CmdResult(cmd=cmd, returncode=0, stdout="", stderr="")


class _FakeEnvManager(EnvManager):
    def __init__(self, kernel: ExecKernel):
        self.k = kernel

    def ensure_venv(self, system_python: str = "python") -> None:
        return None

    def install_requirements(self, include_dev=None) -> None:
        return None

    def venv_python(self) -> str:
        return "/fake/python"


class _FakeLLM(LLMClient):
    def __init__(self, responses):
        # Keep a lightweight in-memory stub; no network calls.
        self.responses = responses
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if self.calls >= len(self.responses):
            resp = self.responses[-1]
        else:
            resp = self.responses[self.calls]
            self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        return json.dumps(resp)


class _ScriptedLLM(LLMClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if self.calls >= len(self.responses):
            raise RuntimeError("LLM had no scripted response")
        out = self.responses[self.calls]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return out


def test_llm_json_repair_eventually_succeeds():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    llm = _ScriptedLLM([
        "not-json",
        '{"name": 123}',
        '{"name": "ok"}',
    ])

    result = asyncio.run(_llm_json(cast(LLMClient, llm), "system", "user", schema, max_repair=2))

    assert result == {"name": "ok"}


def test_llm_json_raises_with_clear_message_when_all_repairs_fail():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    with pytest.raises(ValueError) as exc:
        asyncio.run(
            _llm_json(
                cast(LLMClient, _ScriptedLLM(["bad", '{"name": 123}'])),
                "system",
                "user",
                schema,
                max_repair=0,
            )
        )

    msg = str(exc.value)
    assert "Structured JSON generation failed" in msg
    assert "Last raw output" in msg


def test_llm_json_raises_on_chat_failure_before_parse():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    with pytest.raises(ValueError) as exc:
        asyncio.run(
            _llm_json(
                cast(LLMClient, _ScriptedLLM([RuntimeError("network")]),),
                "system",
                "user",
                schema,
                max_repair=1,
            )
        )

    assert "LLM call failed while generating structured output" in str(exc.value)


class _FakePassingValidation:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.status = "passed"
        self.result = _FakeResult(0)
        self.note = ""
        self.duration_ms = 0
        self.tool_version = "1.0"
        self.error_classification: Optional[str] = None


class _FakePassingValidators:
    calls: list[tuple[str, list[str], list[str], dict[str, Any]]] = []

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        out = []
        for r in results:
            out.append(
                {
                    "name": r.name,
                    "ok": r.ok,
                    "status": r.status,
                    "returncode": r.result.returncode,
                    "duration_ms": r.duration_ms,
                    "tool_version": r.tool_version,
                    "error_classification": r.error_classification,
                    "stdout": "",
                    "stderr": "",
                    "note": r.note,
                }
            )
        return out

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        _FakePassingValidators.calls.append((phase, list(enabled), sorted(soft_validators or []), kwargs))
        return [_FakePassingValidation(name) for name in enabled]


def test_run_loop_repair_escalation_path(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path))

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core task",
                    "goal": "Build core endpoint",
                    "acceptance": [
                        "Add tests for core route",
                        "Validate success and error handling",
                    ],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeValidators)

    ok, _, plan, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=5,
            max_fix_loops_per_task=5,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    assert plan["project"]["type"] == "python_fastapi"
    assert Path(ws.root, ".autodev/task_core_quality.json").exists()
    assert Path(ws.root, ".autodev/task_core_last_validation.json").exists()
    assert Path(ws.root, ".autodev/task_quality_index.json").exists()
    assert len([c for c in _FakeValidators.calls if c[0] == "per_task"]) >= 1


def test_run_loop_end_to_end_reports_and_quality_payloads(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "report-run"))

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
                "quality_level": "balanced",
                "default_artifacts": ["README.md"],
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core service",
                    "goal": "Implement core API endpoint and tests",
                    "acceptance": [
                        "Add tests for API success and error cases",
                        "Validate validation and error handling behavior",
                    ],
                    "files": ["src/app/main.py", "tests/test_api_contract.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "fixer", "summary": "noop", "changes": [], "notes": []},
    ]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)

    ok, prd_struct, plan, last_validation = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "pytest"],
            audit_required=False,
            max_fix_loops_total=5,
            max_fix_loops_per_task=5,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    assert plan["project"]["type"] == "python_fastapi"

    write_report(ws.root, prd_struct, plan, last_validation, ok)

    assert Path(ws.root, ".autodev/task_quality_index.json").exists()
    assert Path(ws.root, ".autodev/task_core_quality.json").exists()
    assert Path(ws.root, ".autodev/task_core_last_validation.json").exists()
    assert Path(ws.root, ".autodev/quality_profile.json").exists()
    assert Path(ws.root, ".autodev/quality_run_summary.json").exists()
    assert Path(ws.root, ".autodev/quality_resolution.json").exists()

    report_text = Path(ws.root, ".autodev/REPORT.md").read_text(encoding="utf-8")
    assert "AUTODEV REPORT" in report_text
    assert "Quality Scorecard" in report_text


def test_run_loop_resolves_gate_profile_by_plan_quality_level(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "quality-gate-run"))

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "strict",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core task",
                    "goal": "Build core endpoint",
                    "acceptance": [
                        "Add tests for core route",
                        "Validate success and error handling",
                    ],
                    "files": ["src/app/main.py", "tests/test_api_contract.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "fixer", "summary": "noop", "changes": [], "notes": []},
    ]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "pytest"],
            audit_required=False,
            max_fix_loops_total=5,
            max_fix_loops_per_task=5,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "per_task_soft": ["docker_build", "pip_audit", "sbom", "semgrep"],
                "final_soft": ["pip_audit", "sbom", "semgrep"],
                "by_level": {
                    "strict": {
                        "per_task_soft": ["docker_build"],
                        "final_soft": [],
                    }
                },
            },
            verbose=False,
        )
    )

    assert ok is True
    profile = json.loads(
        (Path(ws.root) / ".autodev" / "quality_profile.json").read_text(encoding="utf-8")
    )
    assert profile["name"] == "strict"
    assert profile["per_task_soft"] == ["docker_build"]
    assert profile["final_soft"] == []


def test_run_loop_emits_structured_loop_events(monkeypatch, tmp_path):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "logging-events"))
    events: list[dict] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-logger",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-a",
                    "title": "Quick task",
                    "goal": "Add endpoint",
                    "acceptance": ["Add tests for endpoint", "Handle error fallback"],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff", "pytest"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
    ]

    ok, _, _, _ = asyncio.run(
        loop.run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {
                    "per_task": {"soft_fail": ["semgrep"]},
                    "final": {"soft_fail": []},
                },
            },
            verbose=False,
            run_id="run-log-1",
            request_id="req-log-1",
            profile="minimal",
        )
    )

    assert ok is True
    assert any(e.get("event") == "run_enterprise.start" for e in events)
    assert any(e.get("event") == "task.start" and e.get("task_id") == "task-a" for e in events)
    assert any(e.get("event") == "validation.attempt" and e.get("run_id") == "run-log-1" for e in events)
    assert any(e.get("event") == "validation.final_summary" for e in events)
    assert any(e.get("run_id") == "run-log-1" for e in events)


class _FakeSelectiveValidators:
    calls: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        out = []
        for r in results:
            out.append(
                {
                    "name": r.name,
                    "ok": r.ok,
                    "status": r.status,
                    "returncode": r.result.returncode,
                    "duration_ms": r.duration_ms,
                    "tool_version": r.tool_version,
                    "error_classification": r.error_classification,
                    "stdout": "",
                    "stderr": "",
                    "note": r.note,
                }
            )
        return out

    @staticmethod
    def reset() -> None:
        _FakeSelectiveValidators.calls = []

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        _FakeSelectiveValidators.calls.append((phase, ",".join(enabled)))
        if phase != "per_task":
            return [_FakePassingValidation(name) for name in enabled]

        if len([c for c in _FakeSelectiveValidators.calls if c[0] == "per_task"]) == 1:
            return [_FakeValidation("ruff", False, "failed", 1), _FakeValidation("pytest", True, "passed", 0)] if "ruff" in enabled else [_FakePassingValidation(name) for name in enabled]
        return [_FakePassingValidation(name) for name in enabled]

    def run_one(self, name, audit_required=False, phase="task", **kwargs):
        _FakeSelectiveValidators.calls.append((phase + ":one", name))
        return _FakeValidation(name, True, "passed", 0)


class _FakePerfGateValidators:
    calls: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        out = []
        for r in results:
            out.append(
                {
                    "name": r.name,
                    "ok": r.ok,
                    "status": r.status,
                    "returncode": r.result.returncode,
                    "duration_ms": r.duration_ms,
                    "tool_version": r.tool_version,
                    "error_classification": r.error_classification,
                    "stdout": "",
                    "stderr": "",
                    "note": r.note,
                }
            )
        return out

    @staticmethod
    def reset() -> None:
        _FakePerfGateValidators.calls = []

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        _FakePerfGateValidators.calls.append((phase, ",".join(enabled)))
        if phase == "per_task":
            return [_FakePassingValidation(name) for name in enabled]

        if len([c for c in _FakePerfGateValidators.calls if c[0] == "final"]) == 1:
            if "ruff" in enabled:
                failure = _FakeValidation("ruff", False, "failed", 1)
                failure.error_classification = "perf_target_regression"
                return [failure, _FakeValidation("pytest", True, "passed", 0)]
            return [_FakePassingValidation(name) for name in enabled]

        return [_FakePassingValidation(name) for name in enabled]

    def run_one(self, name, audit_required=False, phase="task", **kwargs):
        _FakePerfGateValidators.calls.append((phase + ":one", name))
        return _FakeValidation(name, True, "passed", 0)


def test_run_loop_only_reruns_failed_validators(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "targeted-reruns"))
    _FakeSelectiveValidators.reset()

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core task",
                    "goal": "Build core endpoint",
                    "acceptance": [
                        "Add tests for core route",
                        "Validate success and error handling",
                    ],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff", "pytest"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeSelectiveValidators)

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "pytest"],
            audit_required=False,
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    run_one_calls = [c for c in _FakeSelectiveValidators.calls if c[0] == "per_task:one"]
    assert run_one_calls == [("per_task:one", "ruff")]


def test_run_loop_performance_gate_triggers_targeted_fixes_only_for_perf_validators(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "perf-gate-fixes"))
    _FakePerfGateValidators.reset()

    responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
            "latency_sensitive_paths": ["/forecast"],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "latency_sensitive_paths": ["/forecast"],
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core endpoint",
                    "goal": "Build core endpoint",
                    "acceptance": [
                        "Add tests for core route",
                        "Validate success and error handling",
                    ],
                    "files": ["src/app/main.py", "src/app/slow.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff", "pytest"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePerfGateValidators)

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "pytest"],
            audit_required=False,
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    final_calls = [call for call in _FakePerfGateValidators.calls if call[0] == "final"]
    assert final_calls == [("final", "ruff,pytest"), ("final", "ruff")]


def test_run_loop_reuses_cached_plan_between_runs(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "plan-cache"))

    first_responses = [
        {
            "title": "PRD",
            "goals": [],
            "non_goals": [],
            "features": [],
            "acceptance_criteria": [],
            "nfr": {},
            "constraints": [],
        },
        {
            "project": {
                "type": "python_fastapi",
                "name": "autodev-fake",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Create core task",
                    "goal": "Build core endpoint",
                    "acceptance": [
                        "Add tests for core route",
                        "Validate success and error handling",
                    ],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                }
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
    ]

    second_responses = [{"role": "implementer", "summary": "ok", "changes": [], "notes": []}]

    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)

    first_llm = _FakeLLM(first_responses)
    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, first_llm),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )
    assert ok is True
    assert first_llm.calls == 3

    second_llm = _FakeLLM(second_responses)
    ok2, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, second_llm),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=["semgrep"],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": ["semgrep"]}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )
    assert ok2 is True
    assert second_llm.calls == 1
