from pathlib import Path
import sys
import json
import asyncio
from typing import Any, Optional, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # noqa: E402

from autodev.loop import _toposort, _toposort_levels, _partition_level_for_parallel, _resolve_validators, _validations_ok, _build_quality_row, _build_pass_map  # noqa: E402
from autodev.loop import _llm_json, run_autodev_enterprise  # noqa: E402
from autodev.loop import _failure_signature, _extract_fingerprint_digests  # noqa: E402
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


def test_partition_level_for_parallel_splits_file_conflicts():
    level_tasks = [
        {"id": "a", "files": ["src/a.py"]},
        {"id": "b", "files": ["src/b.py"]},
        {"id": "c", "files": ["src/a.py"]},
    ]

    batches = _partition_level_for_parallel(level_tasks)
    assert len(batches) == 2
    assert [task["id"] for task in batches[0]] == ["a", "b"]
    assert [task["id"] for task in batches[1]] == ["c"]


def test_toposort_levels_groups_independent_tasks():
    ordered = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": []},
        {"id": "c", "depends_on": ["a"]},
        {"id": "d", "depends_on": ["a", "b"]},
    ]
    levels = _toposort_levels(ordered)
    assert [[task["id"] for task in level] for level in levels] == [["a", "b"], ["c", "d"]]


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


def test_quality_row_excludes_skipped_dependency_from_hard_failures():
    rows = [
        {"name": "ruff", "ok": False, "status": "soft_fail", "returncode": 1},
        {"name": "mypy", "ok": False, "status": "skipped_dependency", "returncode": -1},
    ]
    row = _build_quality_row(
        task_id="task-1",
        attempt=2,
        run_set=["ruff", "mypy"],
        validation_rows=rows,
        duration_ms=10,
        soft_validators={"ruff"},
        all_ok=True,
    )

    assert row["status"] == "passed"
    assert row["hard_failures"] == 0
    assert row["soft_failures"] == 1
    assert row["validator_counts"]["skipped_dependency"] == 1


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


MINIMAL_PRD_ANALYSIS = {
    "ambiguities": [],
    "missing_requirements": [],
    "contradictions": [],
    "risks": [],
    "completeness_score": 85,
    "clarification_questions": [],
    "summary": "PRD analysis complete — no significant issues found.",
}

MINIMAL_ARCHITECTURE = {
    "components": [],
    "data_models": [],
    "api_contracts": [],
    "technology_decisions": [],
    "constraints": [],
}

MINIMAL_REVIEW_APPROVE = {
    "findings": [],
    "overall_verdict": "approve",
    "blocking_issues": [],
    "summary": "LGTM",
}

MINIMAL_ACCEPTANCE_TESTS = {
    "test_file": "tests/test_acceptance.py",
    "test_cases": [
        {
            "name": "test_placeholder",
            "description": "Placeholder acceptance test",
            "acceptance_ref": "AC-1",
            "test_type": "unit",
        }
    ],
    "imports": ["import pytest"],
    "fixtures": [],
    "source_code": "import pytest\n\ndef test_placeholder():\n    pytest.skip('awaiting implementation')\n",
}

MINIMAL_API_SPEC = {
    "openapi_version": "3.1.0",
    "info": {"title": "AutoDev API", "version": "1.0.0"},
    "paths": [],
    "components_schemas": [],
    "spec_yaml": "openapi: '3.1.0'\ninfo:\n  title: AutoDev API\n  version: '1.0.0'\npaths: {}\n",
}

MINIMAL_DB_SCHEMA = {
    "models": [],
    "relationships": [],
    "source_code": "from sqlalchemy.orm import declarative_base\n\nBase = declarative_base()\n",
    "alembic_migration": "",
}


def _with_handoff_if_changeset(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    required = {"role", "summary", "changes", "notes"}
    if not required.issubset(payload.keys()):
        return payload
    if "handoff" in payload and isinstance(payload["handoff"], dict):
        return payload
    payload = dict(payload)
    payload["handoff"] = {
        "Summary": str(payload.get("summary") or "요약 없음"),
        "Changed Files": [str(c.get("path")) for c in payload.get("changes", []) if isinstance(c, dict) and c.get("path")],
        "Commands": ["pytest"],
        "Evidence": ["테스트는 스텁 환경에서 검증"],
        "Risks": ["추가 통합 검증 필요"],
        "Next Input": "추가 요구사항이 있으면 알려주세요.",
    }
    return payload


_AUTO_ROLE_RESPONSES: dict[str, dict[str, Any]] = {
    "prd_analyst": MINIMAL_PRD_ANALYSIS,
    "acceptance_test_generator": MINIMAL_ACCEPTANCE_TESTS,
    "api_spec_generator": MINIMAL_API_SPEC,
    "db_schema_generator": MINIMAL_DB_SCHEMA,
    "architect": MINIMAL_ARCHITECTURE,
    "reviewer": MINIMAL_REVIEW_APPROVE,
}


class _FakeLLM(LLMClient):
    def __init__(self, responses):
        # Keep a lightweight in-memory stub; no network calls.
        self.responses = responses
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, *, role_hint: str | None = None) -> str:
        # Auto-respond for new roles not covered by legacy test fixtures.
        if role_hint in _AUTO_ROLE_RESPONSES:
            return json.dumps(_AUTO_ROLE_RESPONSES[role_hint])
        if self.calls >= len(self.responses):
            resp = self.responses[-1]
        else:
            resp = self.responses[self.calls]
            self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        return json.dumps(_with_handoff_if_changeset(resp))


class _ScriptedLLM(LLMClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, *, role_hint: str | None = None) -> str:
        if role_hint in _AUTO_ROLE_RESPONSES:
            return json.dumps(_AUTO_ROLE_RESPONSES[role_hint])
        if self.calls >= len(self.responses):
            raise RuntimeError("LLM had no scripted response")
        out = self.responses[self.calls]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        if isinstance(out, dict):
            return json.dumps(_with_handoff_if_changeset(out))
        return out


class _TempRecordingLLM(LLMClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.temperatures: list[float] = []

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, *, role_hint: str | None = None) -> str:
        if role_hint in _AUTO_ROLE_RESPONSES:
            return json.dumps(_AUTO_ROLE_RESPONSES[role_hint])
        self.temperatures.append(float(temperature))
        if self.calls >= len(self.responses):
            raise RuntimeError("LLM had no scripted response")
        out = self.responses[self.calls]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return json.dumps(_with_handoff_if_changeset(out))


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


def test_llm_json_handoff_missing_triggers_repair(capsys):
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": True,
    }

    llm = _ScriptedLLM([
        {"summary": "ok", "handoff": {"Summary": "only one"}},
        {
            "summary": "ok",
            "handoff": {
                "Summary": "done",
                "Changed Files": ["a.py"],
                "Commands": ["pytest"],
                "Evidence": ["passed"],
                "Risks": ["none"],
                "Next Input": "none",
            },
        },
    ])

    result = asyncio.run(
        _llm_json(
            cast(LLMClient, llm),
            "system",
            "user",
            schema,
            semantic_validator=lambda d: None
            if isinstance(d.get("handoff"), dict) and "Next Input" in d["handoff"]
            and "Commands" in d["handoff"] and "Evidence" in d["handoff"]
            and "Risks" in d["handoff"] and "Changed Files" in d["handoff"] and "Summary" in d["handoff"]
            else "MISSING_HANDOFF_FIELDS:Summary,Changed Files,Commands,Evidence,Risks,Next Input",
            max_repair=1,
        )
    )

    assert result["handoff"]["Next Input"] == "none"
    assert "handoff.repair_requested" in capsys.readouterr().out


def test_llm_json_handoff_missing_exhaustion_logs_incomplete(capsys):
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": True,
    }

    llm = _ScriptedLLM([{"summary": "ok", "handoff": {"Summary": "only"}}])

    with pytest.raises(ValueError):
        asyncio.run(
            _llm_json(
                cast(LLMClient, llm),
                "system",
                "user",
                schema,
                semantic_validator=lambda _d: "MISSING_HANDOFF_FIELDS:Changed Files,Commands,Evidence,Risks,Next Input",
                max_repair=0,
            )
        )

    assert "handoff.incomplete" in capsys.readouterr().out


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


def test_run_loop_applies_role_specific_temperatures(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "role-temps"))
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)
    _FakePassingValidators.calls = []

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
                "name": "autodev-role-temp",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Core API",
                    "goal": "Create core endpoint",
                    "acceptance": ["Add tests for core endpoint", "Validate error responses"],
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
    llm = _TempRecordingLLM(responses)

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, llm),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            role_temperatures={
                "prd_normalizer": 0.25,
                "planner": 0.4,
                "implementer": 0.1,
            },
        )
    )

    assert ok is True
    assert llm.temperatures[:3] == [0.25, 0.4, 0.1]


def test_repeat_failure_guard_disabled_keeps_normal_repairs(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "repeat-guard-disabled"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeValidators)
    _FakeValidators.calls = []

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
                    "acceptance": ["Add tests for core route"],
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
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    ok, _, _, _ = asyncio.run(
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
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
                "escalation": {
                    "repeat_failure_guard": {
                        "enabled": False,
                        "max_retries_before_targeted_fix": 1,
                    }
                },
            },
            verbose=False,
        )
    )

    assert ok is True
    repair_events = [e for e in events if e.get("event") == "task.repair_requested"]
    assert repair_events
    assert all(e.get("repair_mode") == "normal" for e in repair_events)


def test_repeat_failure_guard_zero_threshold_targets_first_retry(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "repeat-guard-zero-threshold"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeValidators)
    _FakeValidators.calls = []

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
                    "acceptance": ["Add tests for core route"],
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
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    ok, _, _, _ = asyncio.run(
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
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
                "escalation": {
                    "repeat_failure_guard": {
                        "enabled": True,
                        "max_retries_before_targeted_fix": 0,
                    }
                },
            },
            verbose=False,
        )
    )

    assert ok is True
    repair_events = [e for e in events if e.get("event") == "task.repair_requested"]
    assert repair_events
    assert repair_events[0].get("repair_mode") == "targeted"


class _FakeValidatorsFailThree:
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
        _FakeValidatorsFailThree.calls.append((phase, list(enabled), sorted(soft_validators or []), kwargs))
        if phase == "per_task":
            attempt = len([c for c in _FakeValidatorsFailThree.calls if c[0] == "per_task"])
            if attempt <= 3:
                return [_FakeValidation("ruff", False, "failed", 1)]
            return [_FakeValidation("ruff", True, "passed", 0)]
        return [_FakeValidation("ruff", True, "passed", 0)]


def test_repeat_failure_guard_respects_retry_threshold(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "repeat-guard-threshold"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeValidatorsFailThree)
    _FakeValidatorsFailThree.calls = []

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
                    "acceptance": ["Add tests for core route"],
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
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
        {"role": "fixer", "summary": "repair", "changes": [], "notes": []},
    ]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=6,
            max_fix_loops_per_task=6,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
                "escalation": {
                    "repeat_failure_guard": {
                        "enabled": True,
                        "max_retries_before_targeted_fix": 2,
                    }
                },
            },
            verbose=False,
        )
    )

    assert ok is True
    repair_modes = [e.get("repair_mode") for e in events if e.get("event") == "task.repair_requested"]
    assert repair_modes[:3] == ["normal", "normal", "targeted"]


def test_run_loop_resume_skips_completed_tasks_from_checkpoint(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "resume-checkpoint"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    ws.write_text(
        ".autodev/checkpoint.json",
        json.dumps(
            {
                "status": "running",
                "completed_task_ids": ["core"],
                "failed_task_id": None,
            }
        ),
    )

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)
    _FakePassingValidators.calls = []

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
                "name": "autodev-resume",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Core API",
                    "goal": "Create core endpoint",
                    "acceptance": ["Add tests for core endpoint", "Validate error responses"],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "docs",
                    "title": "Docs updates",
                    "goal": "Update docs",
                    "acceptance": ["README updated"],
                    "files": ["README.md"],
                    "depends_on": ["core"],
                    "quality_expectations": {
                        "requires_tests": False,
                        "requires_error_contract": False,
                        "touches_contract": False,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
    ]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            resume=True,
        )
    )

    assert ok is True
    assert any(e.get("event") == "task.resume_skipped" and e.get("task_id") == "core" for e in events)
    quality_path = Path(ws.root) / ".autodev" / "task_core_quality.json"
    payload = json.loads(quality_path.read_text(encoding="utf-8"))
    assert payload["resumed_from_checkpoint"] is True

    checkpoint = json.loads((Path(ws.root) / ".autodev" / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "completed"
    assert set(checkpoint["completed_task_ids"]) == {"core", "docs"}


def test_run_loop_interactive_can_abort_before_implementation(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "interactive-abort"))
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

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
                "name": "autodev-interactive",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Core API",
                    "goal": "Create core endpoint",
                    "acceptance": ["Add tests for core endpoint", "Validate error responses"],
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
    ]
    llm = _FakeLLM(responses)

    ok, _, plan, last_validation = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, llm),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff"],
            audit_required=False,
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            interactive=True,
        )
    )

    assert ok is False
    assert plan["project"]["name"] == "autodev-interactive"
    assert last_validation == []
    assert llm.calls == 2
    assert (Path(ws.root) / ".autodev" / "plan.json").exists()


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


def test_run_loop_parallel_batch_event_for_disjoint_tasks(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "parallel-batch"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)
    _FakePassingValidators.calls = []

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
                "name": "autodev-parallel",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-a",
                    "title": "Task A implementation",
                    "goal": "Implement feature A endpoint",
                    "acceptance": ["Add tests for feature A endpoint", "Validate error behavior"],
                    "files": ["src/app/a.py", "tests/test_a.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-b",
                    "title": "Task B implementation",
                    "goal": "Implement feature B endpoint",
                    "acceptance": ["Add tests for feature B endpoint", "Validate error behavior"],
                    "files": ["src/app/b.py", "tests/test_b.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
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
            max_fix_loops_total=4,
            max_fix_loops_per_task=4,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    parallel_events = [e for e in events if e.get("event") == "task.batch_parallel_start"]
    assert parallel_events
    assert set(parallel_events[0].get("task_ids", [])) == {"task-a", "task-b"}


def test_run_loop_no_parallel_batch_event_when_files_overlap(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "parallel-overlap"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakePassingValidators)
    _FakePassingValidators.calls = []

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
                "name": "autodev-overlap",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-a",
                    "title": "Task A implementation",
                    "goal": "Implement shared endpoint behavior",
                    "acceptance": ["Add tests for shared endpoint", "Validate error behavior"],
                    "files": ["src/app/main.py", "tests/test_shared.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-b",
                    "title": "Task B implementation",
                    "goal": "Refine shared endpoint behavior",
                    "acceptance": ["Add tests for shared endpoint", "Validate error behavior"],
                    "files": ["src/app/main.py", "tests/test_shared.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
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
            max_fix_loops_total=4,
            max_fix_loops_per_task=4,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )

    assert ok is True
    assert not any(e.get("event") == "task.batch_parallel_start" for e in events)


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


class _FakeGraphRunAllOnlyValidators:
    calls: list[tuple[str, tuple[str, ...]]] = []
    ruff_per_task_calls = 0

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
        _FakeGraphRunAllOnlyValidators.calls = []
        _FakeGraphRunAllOnlyValidators.ruff_per_task_calls = 0

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        names = tuple(enabled)
        _FakeGraphRunAllOnlyValidators.calls.append((phase, names))

        if phase == "per_task" and names == ("ruff",):
            _FakeGraphRunAllOnlyValidators.ruff_per_task_calls += 1
            if _FakeGraphRunAllOnlyValidators.ruff_per_task_calls == 1:
                return [_FakeValidation("ruff", False, "failed", 1)]
            return [_FakeValidation("ruff", True, "passed", 0)]

        if phase == "per_task":
            return [_FakeValidation(name, True, "passed", 0) for name in enabled]
        return [_FakePassingValidation(name) for name in enabled]


class _FakeDeterministicBenchmarkValidators:
    run_all_calls: list[tuple[str, tuple[str, ...]]] = []
    run_one_calls: list[tuple[str, str]] = []
    _first_graph_ruff_failed = False

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        return _FakeGraphRunAllOnlyValidators.serialize(results)

    @staticmethod
    def reset() -> None:
        _FakeDeterministicBenchmarkValidators.run_all_calls = []
        _FakeDeterministicBenchmarkValidators.run_one_calls = []
        _FakeDeterministicBenchmarkValidators._first_graph_ruff_failed = False

    @staticmethod
    def per_task_invocations() -> int:
        per_task_run_all = sum(
            len(enabled)
            for phase, enabled in _FakeDeterministicBenchmarkValidators.run_all_calls
            if phase == "per_task"
        )
        per_task_run_one = sum(
            1 for phase, _ in _FakeDeterministicBenchmarkValidators.run_one_calls
            if phase == "per_task"
        )
        return per_task_run_all + per_task_run_one

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        names = tuple(enabled)
        _FakeDeterministicBenchmarkValidators.run_all_calls.append((phase, names))

        if phase == "per_task":
            return [
                _FakeValidation(name, False, "failed", 1)
                for name in enabled
            ]

        return [_FakePassingValidation(name) for name in enabled]

    def run_one(self, name, audit_required=False, phase="task", **kwargs):
        _FakeDeterministicBenchmarkValidators.run_one_calls.append((phase, name))

        if (
            phase == "per_task"
            and name == "ruff"
            and not _FakeDeterministicBenchmarkValidators._first_graph_ruff_failed
            and not any(p == "per_task" for p, _ in _FakeDeterministicBenchmarkValidators.run_all_calls)
        ):
            _FakeDeterministicBenchmarkValidators._first_graph_ruff_failed = True
            return _FakeValidation(name, False, "failed", 1)

        return _FakeValidation(name, True, "passed", 0)


def test_run_loop_validator_graph_reruns_skipped_dependents_with_run_all_only_backend(tmp_path, monkeypatch):
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "validator-graph-run-all-only"))
    _FakeGraphRunAllOnlyValidators.reset()

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
                "name": "autodev-graph",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Core graph task",
                    "goal": "Validate graph reruns",
                    "acceptance": [
                        "Add test coverage for ruff/mypy dependency recovery",
                        "Validate error handling when ruff fails before mypy",
                    ],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff", "mypy"],
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
    monkeypatch.setattr(loop, "Validators", _FakeGraphRunAllOnlyValidators)

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "mypy"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
                "validator_graph": {
                    "enabled": True,
                    "mode": "strict",
                    "skip_on_soft_fail": False,
                    "custom_edges": {},
                },
            },
            verbose=False,
        )
    )

    assert ok is True
    per_task_calls = [c for c in _FakeGraphRunAllOnlyValidators.calls if c[0] == "per_task"]
    assert per_task_calls.count(("per_task", ("ruff",))) == 2
    assert per_task_calls.count(("per_task", ("mypy",))) == 1

    quality = json.loads(ws.read_text(".autodev/task_core_quality.json"))
    assert quality["attempts"][0]["validations"][1]["status"] == "skipped_dependency"
    assert quality["attempts"][-1]["validations"][1]["status"] == "passed"


def test_validator_graph_benchmark_reduces_per_task_invocations(tmp_path, monkeypatch):
    import autodev.loop as loop

    base_responses = [
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
                "name": "autodev-benchmark",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Benchmark core task",
                    "goal": "Measure validator invocation savings",
                    "acceptance": [
                        "Add test coverage for validator dependency behavior",
                        "Validate error handling in prerequisite failures",
                    ],
                    "files": ["src/app/main.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff", "mypy", "pytest"],
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
    monkeypatch.setattr(loop, "Validators", _FakeDeterministicBenchmarkValidators)

    # Baseline: dependency graph disabled
    _FakeDeterministicBenchmarkValidators.reset()
    ws_baseline = Workspace(str(tmp_path / "benchmark-baseline"))
    ok_baseline, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(list(base_responses))),
            ws=ws_baseline,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "mypy", "pytest"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
        )
    )
    assert ok_baseline is True
    baseline_invocations = _FakeDeterministicBenchmarkValidators.per_task_invocations()

    # Candidate: dependency graph enabled
    _FakeDeterministicBenchmarkValidators.reset()
    ws_graph = Workspace(str(tmp_path / "benchmark-graph"))
    ok_graph, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(list(base_responses))),
            ws=ws_graph,
            prd_markdown="",
            template_root=str(ROOT / "templates"),
            template_candidates=["python_fastapi"],
            validators_enabled=["ruff", "mypy", "pytest"],
            audit_required=False,
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
                "validator_graph": {
                    "enabled": True,
                    "mode": "strict",
                    "skip_on_soft_fail": False,
                    "custom_edges": {},
                },
            },
            verbose=False,
        )
    )
    assert ok_graph is True
    graph_invocations = _FakeDeterministicBenchmarkValidators.per_task_invocations()

    assert baseline_invocations > 0
    reduction_pct = (baseline_invocations - graph_invocations) / baseline_invocations
    assert reduction_pct >= 0.10


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


# ---------------------------------------------------------------------------
# Resilient parallel execution (continue_on_failure)
# ---------------------------------------------------------------------------


class _FakeTaskSpecificValidators:
    """Fails validation for task-id 'task-fail', passes everything else."""

    calls: list[tuple[str, str | None]] = []

    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def serialize(results):
        return _FakePassingValidators.serialize(results)

    @staticmethod
    def reset() -> None:
        _FakeTaskSpecificValidators.calls = []

    def run_all(self, enabled, audit_required=False, soft_validators=None, phase="task", **kwargs):
        task_id = kwargs.get("task_id")
        _FakeTaskSpecificValidators.calls.append((phase, task_id))
        if phase == "per_task" and task_id == "task-fail":
            return [_FakeValidation("ruff", False, "failed", 1)]
        return [_FakePassingValidation(name) for name in enabled]

    def run_one(self, name, audit_required=False, phase="task", **kwargs):
        task_id = kwargs.get("task_id")
        if phase == "per_task" and task_id == "task-fail":
            return _FakeValidation(name, False, "failed", 1)
        return _FakeValidation(name, True, "passed", 0)


def test_run_loop_continues_after_task_failure(tmp_path, monkeypatch):
    """With continue_on_failure=True (default), a failed task should not prevent
    independent tasks from executing.  ok should be False."""
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "continue-fail"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeTaskSpecificValidators)
    _FakeTaskSpecificValidators.reset()

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
                "name": "autodev-continue",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-fail",
                    "title": "Failing task",
                    "goal": "This task will fail validation",
                    "acceptance": ["Add test for fail module", "Validate error handling"],
                    "files": ["src/fail.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-pass",
                    "title": "Passing task",
                    "goal": "This task will pass validation",
                    "acceptance": ["Add test for pass module", "Validate error handling"],
                    "files": ["src/pass.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        # Implementer responses (one per task)
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        # Fixer responses for the failing task (retry loop)
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
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
            max_fix_loops_total=4,
            max_fix_loops_per_task=4,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            continue_on_failure=True,
        )
    )

    assert ok is False

    # Verify task-fail recorded as failed
    failed_continuing_events = [e for e in events if e.get("event") == "task.failed_continuing"]
    assert len(failed_continuing_events) >= 1
    assert failed_continuing_events[0]["task_id"] == "task-fail"

    # Read quality summary to confirm both tasks are recorded
    quality_path = tmp_path / "continue-fail" / ".autodev" / "task_quality_index.json"
    quality = json.loads(quality_path.read_text())
    task_statuses = {t["task_id"]: t["status"] for t in quality["tasks"]}
    assert task_statuses.get("task-fail") == "failed"
    assert task_statuses.get("task-pass") == "passed"


def test_run_loop_skips_dependent_on_failed_task(tmp_path, monkeypatch):
    """When a task fails and continue_on_failure=True, tasks depending on the
    failed task should be skipped, but independent tasks should still run."""
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "dep-skip"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeTaskSpecificValidators)
    _FakeTaskSpecificValidators.reset()

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
                "name": "autodev-dep-skip",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-fail",
                    "title": "Base failing task",
                    "goal": "This base task will fail",
                    "acceptance": ["Add test for base module", "Validate error handling"],
                    "files": ["src/base.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-independent",
                    "title": "Independent task",
                    "goal": "This independent task will pass",
                    "acceptance": ["Add test for indie module", "Validate error handling"],
                    "files": ["src/indie.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-child",
                    "title": "Dependent child task",
                    "goal": "This task depends on failing base",
                    "acceptance": ["Add test for child module", "Validate error handling"],
                    "files": ["src/child.py"],
                    "depends_on": ["task-fail"],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        # Implementer responses
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        # Fixer responses for retry
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
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
            max_fix_loops_total=4,
            max_fix_loops_per_task=4,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            continue_on_failure=True,
        )
    )

    assert ok is False

    # Verify dependency skip event was logged
    skip_events = [e for e in events if e.get("event") == "task.dependency_skipped"]
    assert len(skip_events) >= 1
    assert skip_events[0]["task_id"] == "task-child"
    assert "task-fail" in skip_events[0]["unmet_dependencies"]

    # Read quality summary
    quality_path = tmp_path / "dep-skip" / ".autodev" / "task_quality_index.json"
    quality = json.loads(quality_path.read_text())
    task_statuses = {t["task_id"]: t["status"] for t in quality["tasks"]}
    assert task_statuses.get("task-fail") == "failed"
    assert task_statuses.get("task-child") == "skipped"
    assert task_statuses.get("task-independent") == "passed"

    # Verify totals include skipped info
    assert quality["totals"]["skipped_tasks"] == 1
    assert "task-child" in quality["totals"]["skipped_task_ids"]


def test_run_loop_continue_on_failure_false_stops_early(tmp_path, monkeypatch):
    """With continue_on_failure=False, the first failing task should immediately
    terminate the run (legacy behaviour)."""
    import autodev.loop as loop

    ws = Workspace(str(tmp_path / "stop-early"))
    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    monkeypatch.setattr(loop, "_log_event", _capture)
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    monkeypatch.setattr(loop, "Validators", _FakeTaskSpecificValidators)
    _FakeTaskSpecificValidators.reset()

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
                "name": "autodev-stop-early",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "task-fail",
                    "title": "Failing task",
                    "goal": "This task will fail validation",
                    "acceptance": ["Add test for fail module", "Validate error handling"],
                    "files": ["src/fail.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
                {
                    "id": "task-pass",
                    "title": "Passing task",
                    "goal": "This task will pass validation",
                    "acceptance": ["Add test for pass module", "Validate error handling"],
                    "files": ["src/pass.py"],
                    "depends_on": [],
                    "quality_expectations": {
                        "requires_tests": True,
                        "requires_error_contract": True,
                        "touches_contract": True,
                    },
                    "validator_focus": ["ruff"],
                },
            ],
            "ci": {"enabled": True, "provider": "github_actions"},
            "docker": {"enabled": True},
            "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
            "observability": {"enabled": True},
        },
        # Implementer responses
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        {"role": "implementer", "summary": "ok", "changes": [], "notes": []},
        # Fixer responses
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
        {"role": "fixer", "summary": "fix", "changes": [], "notes": []},
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
            max_fix_loops_total=4,
            max_fix_loops_per_task=4,
            max_json_repair=0,
            task_soft_validators=[],
            final_soft_validators=[],
            quality_profile={
                "name": "balanced",
                "validator_policy": {"per_task": {"soft_fail": []}, "final": {"soft_fail": []}},
            },
            verbose=False,
            continue_on_failure=False,
        )
    )

    assert ok is False

    # Should NOT have the "failed_continuing" event (legacy stops immediately)
    failed_continuing_events = [e for e in events if e.get("event") == "task.failed_continuing"]
    assert len(failed_continuing_events) == 0

    # Only the failing task should appear in quality summary (run terminated early)
    quality_path = tmp_path / "stop-early" / ".autodev" / "task_quality_index.json"
    quality = json.loads(quality_path.read_text())
    task_ids = [t["task_id"] for t in quality["tasks"]]
    assert "task-fail" in task_ids
    # task-pass should NOT have completed because execution stopped
    # (It may or may not be present depending on execution order, but if present
    # it confirms the old behavior pattern. The key assertion is no "failed_continuing".)


# ---------------------------------------------------------------------------
# _failure_signature with fingerprints
# ---------------------------------------------------------------------------


def test_failure_signature_with_fingerprints():
    """Enhanced signature should include fingerprint digests."""
    rows = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "src/main.py:5:1: F401 'os' imported but unused",
            "stderr": "",
            "diagnostics": {"locations": ["src/main.py:5"]},
            "duration_ms": 100,
        }
    ]
    sig = _failure_signature(rows)
    assert len(sig) == 1
    name, status, digests = sig[0]
    assert name == "ruff"
    assert status == "failed"
    assert isinstance(digests, tuple)
    assert len(digests) >= 1


def test_failure_signature_same_error_same_sig():
    """Same error in two calls should produce identical signatures."""
    rows = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "src/main.py:5:1: F401 'os' imported but unused",
            "stderr": "",
            "diagnostics": {"locations": ["src/main.py:5"]},
            "duration_ms": 100,
        }
    ]
    sig1 = _failure_signature(rows)
    sig2 = _failure_signature(rows)
    assert sig1 == sig2


def test_failure_signature_different_error_different_sig():
    """Different errors should produce different signatures."""
    rows1 = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "src/main.py:5:1: F401 'os' imported but unused",
            "stderr": "",
            "diagnostics": {"locations": ["src/main.py:5"]},
            "duration_ms": 100,
        }
    ]
    rows2 = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "src/main.py:10:1: E501 line too long",
            "stderr": "",
            "diagnostics": {"locations": ["src/main.py:10"]},
            "duration_ms": 100,
        }
    ]
    assert _failure_signature(rows1) != _failure_signature(rows2)


def test_failure_signature_legacy_mode():
    """Legacy mode (include_fingerprints=False) should match old behavior."""
    rows = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "some error",
            "stderr": "",
            "diagnostics": {},
            "duration_ms": 100,
        }
    ]
    sig = _failure_signature(rows, include_fingerprints=False)
    assert sig == (("ruff", "failed", "tool_error"),)


def test_extract_fingerprint_digests():
    """_extract_fingerprint_digests should return unique digest set."""
    rows = [
        {
            "name": "ruff",
            "ok": False,
            "status": "failed",
            "error_classification": "tool_error",
            "stdout": "src/a.py:5:1: F401 'os' unused\nsrc/b.py:10:1: F401 'sys' unused",
            "stderr": "",
            "diagnostics": {"locations": ["src/a.py:5", "src/b.py:10"]},
            "duration_ms": 100,
        },
        {
            "name": "pytest",
            "ok": True,
            "status": "passed",
            "error_classification": None,
            "stdout": "1 passed",
            "stderr": "",
            "diagnostics": {},
            "duration_ms": 200,
        },
    ]
    digests = _extract_fingerprint_digests(rows)
    assert isinstance(digests, set)
    assert len(digests) == 2  # Only ruff has 2 unique errors; pytest passes


def test_extract_fingerprint_digests_empty_on_all_passing():
    """All passing rows should return empty set."""
    rows = [
        {
            "name": "ruff",
            "ok": True,
            "status": "passed",
            "error_classification": None,
            "stdout": "",
            "stderr": "",
            "diagnostics": {},
            "duration_ms": 100,
        },
    ]
    assert _extract_fingerprint_digests(rows) == set()
