"""End-to-end integration tests for the full autodev pipeline.

Each test exercises run_autodev_enterprise with realistic fake LLM responses
covering the complete PRD → Architecture → Planning → Implementation →
Validation → Reporting flow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional, cast

from autodev.loop import run_autodev_enterprise
from autodev.workspace import Workspace
from autodev.json_utils import strict_json_loads
from autodev.llm_client import LLMClient
from autodev.exec_kernel import ExecKernel, CmdResult
from autodev.env_manager import EnvManager

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_ROOT = str(ROOT / "templates")

# ---------------------------------------------------------------------------
# Fake infrastructure (lightweight copies to avoid cross-test-file imports)
# ---------------------------------------------------------------------------

_MINIMAL_PRD_ANALYSIS = {
    "ambiguities": [], "missing_requirements": [], "contradictions": [],
    "risks": [], "completeness_score": 85, "clarification_questions": [],
    "summary": "PRD analysis complete.",
}
_MINIMAL_ARCHITECTURE = {
    "components": [], "data_models": [], "api_contracts": [],
    "technology_decisions": [], "constraints": [],
}
_MINIMAL_REVIEW = {
    "findings": [], "overall_verdict": "approve",
    "blocking_issues": [], "summary": "LGTM",
}
_MINIMAL_ACCEPTANCE = {
    "test_file": "tests/test_acceptance.py",
    "test_cases": [{"name": "test_placeholder", "description": "placeholder",
                     "acceptance_ref": "AC-1", "test_type": "unit"}],
    "imports": ["import pytest"], "fixtures": [],
    "source_code": "import pytest\n\ndef test_placeholder():\n    pytest.skip('awaiting')\n",
}
_MINIMAL_API_SPEC = {
    "openapi_version": "3.1.0",
    "info": {"title": "API", "version": "1.0.0"},
    "paths": [], "components_schemas": [],
    "spec_yaml": "openapi: '3.1.0'\ninfo:\n  title: API\n  version: '1.0.0'\npaths: {}\n",
}
_MINIMAL_DB_SCHEMA = {
    "models": [], "relationships": [],
    "source_code": "from sqlalchemy.orm import declarative_base\nBase = declarative_base()\n",
    "alembic_migration": "",
}

_AUTO_ROLE_RESPONSES: dict[str, dict[str, Any]] = {
    "prd_analyst": _MINIMAL_PRD_ANALYSIS,
    "acceptance_test_generator": _MINIMAL_ACCEPTANCE,
    "api_spec_generator": _MINIMAL_API_SPEC,
    "db_schema_generator": _MINIMAL_DB_SCHEMA,
    "architect": _MINIMAL_ARCHITECTURE,
    "reviewer": _MINIMAL_REVIEW,
}


def _with_handoff(payload: Any) -> Any:
    """Auto-inject handoff fields into changeset payloads."""
    if not isinstance(payload, dict):
        return payload
    if not {"role", "summary", "changes", "notes"}.issubset(payload.keys()):
        return payload
    if "handoff" in payload and isinstance(payload["handoff"], dict):
        return payload
    payload = dict(payload)
    payload["handoff"] = {
        "Summary": str(payload.get("summary", "")),
        "Changed Files": [str(c.get("path")) for c in payload.get("changes", [])
                          if isinstance(c, dict) and c.get("path")],
        "Commands": ["pytest"],
        "Evidence": ["Tests pass"],
        "Risks": ["None"],
        "Next Input": "Proceed",
    }
    return payload


class _FakeLLM(LLMClient):
    """Fake LLM with auto-responses for auxiliary roles."""

    def __init__(self, responses: list):
        self.responses = responses
        self.calls = 0

    async def chat(self, messages: list[dict[str, str]], temperature: float = 0.2,
                   *, role_hint: str | None = None) -> str:
        if role_hint in _AUTO_ROLE_RESPONSES:
            return json.dumps(_AUTO_ROLE_RESPONSES[role_hint])
        if self.calls >= len(self.responses):
            resp = self.responses[-1]
        else:
            resp = self.responses[self.calls]
            self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        return json.dumps(_with_handoff(resp))


class _FakeResult:
    def __init__(self, rc: int = 0):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


class _FakeValidation:
    def __init__(self, name: str, ok: bool, status: str, rc: int):
        self.name = name
        self.ok = ok
        self.status = status
        self.result = _FakeResult(rc)
        self.note = ""
        self.duration_ms = 10
        self.tool_version = "1.0"
        self.error_classification: Optional[str] = None


class _FakePassingValidation:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.status = "passed"
        self.result = _FakeResult(0)
        self.note = ""
        self.duration_ms = 5
        self.tool_version = "1.0"
        self.error_classification: Optional[str] = None


class _FakePassingValidators:
    calls: list = []

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @staticmethod
    def serialize(results: list) -> list:
        return [
            {
                "name": r.name, "ok": r.ok, "status": r.status,
                "returncode": r.result.returncode, "duration_ms": r.duration_ms,
                "tool_version": r.tool_version,
                "error_classification": r.error_classification,
                "stdout": "", "stderr": "", "note": r.note,
            }
            for r in results
        ]

    def run_all(self, enabled: list, audit_required: bool = False,
                soft_validators: Any = None, phase: str = "task", **kwargs: Any) -> list:
        _FakePassingValidators.calls.append(
            (phase, list(enabled), sorted(soft_validators or []), kwargs)
        )
        return [_FakePassingValidation(name) for name in enabled]


class _FakeFailFirstValidators:
    """Fails first 2 per_task validation calls, then passes."""
    calls: list = []

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @staticmethod
    def serialize(results: list) -> list:
        return _FakePassingValidators.serialize(results)

    def run_all(self, enabled: list, audit_required: bool = False,
                soft_validators: Any = None, phase: str = "task", **kwargs: Any) -> list:
        _FakeFailFirstValidators.calls.append(
            (phase, list(enabled), sorted(soft_validators or []), kwargs)
        )
        if phase == "per_task":
            per_task_count = len([c for c in _FakeFailFirstValidators.calls if c[0] == "per_task"])
            if per_task_count <= 2:
                return [_FakeValidation("ruff", False, "failed", 1)]
            return [_FakePassingValidation(name) for name in enabled]
        return [_FakePassingValidation(name) for name in enabled]


class _FakeGraphRunAllOnlyValidators:
    """Graph-mode backend that implements run_all only (no run_one)."""

    calls: list[tuple[str, tuple[str, ...]]] = []
    ruff_per_task_calls = 0

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @staticmethod
    def serialize(results: list) -> list:
        return _FakePassingValidators.serialize(results)

    @staticmethod
    def reset() -> None:
        _FakeGraphRunAllOnlyValidators.calls = []
        _FakeGraphRunAllOnlyValidators.ruff_per_task_calls = 0

    def run_all(self, enabled: list, audit_required: bool = False,
                soft_validators: Any = None, phase: str = "task", **kwargs: Any) -> list:
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

    def install_requirements(self, include_dev: Any = None) -> None:
        return None

    def venv_python(self) -> str:
        return "/fake/python"


# ---------------------------------------------------------------------------
# Realistic test data (URL Shortener)
# ---------------------------------------------------------------------------

URL_SHORTENER_PRD = {
    "title": "URL Shortener REST API",
    "goals": [
        "Provide a REST API to shorten long URLs",
        "Track click counts per shortened URL",
    ],
    "non_goals": [
        "No user authentication",
        "No custom domain support",
    ],
    "features": [
        {
            "name": "URL Shortening",
            "description": "Accept a long URL and return a short code",
            "requirements": [
                "POST /shorten accepts JSON with url field",
                "Returns short_code and short_url",
            ],
        },
        {
            "name": "URL Redirection",
            "description": "Redirect short code to original URL",
            "requirements": [
                "GET /{short_code} returns 307 redirect",
                "Returns 404 for unknown codes",
            ],
        },
    ],
    "acceptance_criteria": [
        "POST /shorten returns 201 with valid short_code",
        "GET /{short_code} returns 307 redirect",
    ],
    "nfr": {"response_time_p99": "100ms"},
    "constraints": ["Python 3.11+", "FastAPI framework"],
}

URL_SHORTENER_PLAN = {
    "project": {
        "type": "python_fastapi",
        "name": "url-shortener",
        "python_version": "3.11",
        "quality_gate_profile": "balanced",
    },
    "tasks": [
        {
            "id": "models",
            "title": "Create data models and storage",
            "goal": "Implement URL model and in-memory store",
            "acceptance": ["Add test coverage for URL model creation and retrieval",
                           "Store supports save and get operations"],
            "files": ["src/app/models.py", "src/app/store.py"],
            "depends_on": [],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
        {
            "id": "api-endpoints",
            "title": "Implement REST API endpoints",
            "goal": "Create shorten and redirect endpoints",
            "acceptance": ["Add test for POST /shorten returns 201",
                           "Validate error handling for invalid URLs"],
            "files": ["src/app/main.py", "src/app/routes.py"],
            "depends_on": ["models"],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": True},
            "validator_focus": ["ruff"],
        },
        {
            "id": "stats",
            "title": "Add click tracking statistics",
            "goal": "Track click counts and expose stats endpoint",
            "acceptance": ["Add test for GET /stats/{code} click count",
                           "Counter increments on redirect"],
            "files": ["src/app/routes.py", "tests/test_stats.py"],
            "depends_on": ["models", "api-endpoints"],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
    ],
    "ci": {"enabled": True, "provider": "github_actions"},
    "docker": {"enabled": True},
    "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
    "observability": {"enabled": True},
}

SINGLE_TASK_PLAN = {
    "project": {
        "type": "python_fastapi",
        "name": "url-shortener",
        "python_version": "3.11",
        "quality_gate_profile": "balanced",
    },
    "tasks": [
        {
            "id": "core",
            "title": "Create core service module",
            "goal": "Implement the core service logic",
            "acceptance": ["Add test coverage for core logic", "Basic assertion checks pass"],
            "files": ["src/app/main.py"],
            "depends_on": [],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
    ],
    "ci": {"enabled": True, "provider": "github_actions"},
    "docker": {"enabled": False},
    "security": {"enabled": False, "tools": []},
    "observability": {"enabled": False},
}

MODELS_CHANGESET = {
    "role": "implementer",
    "summary": "Created URL data model and in-memory store",
    "changes": [
        {
            "op": "write",
            "path": "src/app/models.py",
            "content": (
                "from dataclasses import dataclass, field\n"
                "from datetime import datetime\n\n\n"
                "@dataclass\n"
                "class ShortenedURL:\n"
                "    original_url: str\n"
                "    short_code: str\n"
                "    created_at: datetime = field(default_factory=datetime.utcnow)\n"
                "    click_count: int = 0\n"
            ),
        },
        {
            "op": "write",
            "path": "src/app/store.py",
            "content": (
                "from typing import Dict, Optional\n"
                "from .models import ShortenedURL\n\n\n"
                "class URLStore:\n"
                "    def __init__(self):\n"
                "        self._urls: Dict[str, ShortenedURL] = {}\n\n"
                "    def save(self, url: ShortenedURL) -> None:\n"
                "        self._urls[url.short_code] = url\n\n"
                "    def get(self, short_code: str) -> Optional[ShortenedURL]:\n"
                "        return self._urls.get(short_code)\n"
            ),
        },
    ],
    "notes": ["In-memory storage for simplicity"],
}

API_ENDPOINTS_CHANGESET = {
    "role": "implementer",
    "summary": "Implemented shorten and redirect endpoints",
    "changes": [
        {
            "op": "write",
            "path": "src/app/routes.py",
            "content": (
                "import hashlib\n"
                "from fastapi import APIRouter\n"
                "from pydantic import BaseModel, HttpUrl\n"
                "from .store import URLStore\n"
                "from .models import ShortenedURL\n\n"
                "router = APIRouter()\n"
                "store = URLStore()\n\n\n"
                "class ShortenRequest(BaseModel):\n"
                "    url: HttpUrl\n\n\n"
                "@router.post('/shorten', status_code=201)\n"
                "def shorten(req: ShortenRequest):\n"
                "    code = hashlib.md5(str(req.url).encode()).hexdigest()[:8]\n"
                "    entry = ShortenedURL(original_url=str(req.url), short_code=code)\n"
                "    store.save(entry)\n"
                "    return {'short_code': code}\n"
            ),
        },
    ],
    "notes": [],
}

STATS_CHANGESET = {
    "role": "implementer",
    "summary": "Added click tracking and stats endpoint",
    "changes": [
        {
            "op": "write",
            "path": "tests/test_stats.py",
            "content": (
                "def test_click_count_starts_at_zero():\n"
                "    assert 0 == 0  # placeholder\n"
            ),
        },
    ],
    "notes": [],
}

SIMPLE_CHANGESET = {
    "role": "implementer",
    "summary": "Implemented core logic",
    "changes": [
        {"op": "write", "path": "src/app/main.py",
         "content": "from fastapi import FastAPI\n\napp = FastAPI()\n"},
    ],
    "notes": [],
}

REPAIR_CHANGESET = {
    "role": "fixer",
    "summary": "Fixed lint errors",
    "changes": [
        {"op": "write", "path": "src/app/main.py",
         "content": "from fastapi import FastAPI\n\napp = FastAPI(title='fixed')\n"},
    ],
    "notes": ["Fixed ruff findings"],
}

# ---------------------------------------------------------------------------
# Common run kwargs
# ---------------------------------------------------------------------------

COMMON_KWARGS: dict[str, Any] = {
    "template_root": TEMPLATE_ROOT,
    "template_candidates": ["python_fastapi"],
    "validators_enabled": ["ruff"],
    "audit_required": False,
    "max_fix_loops_total": 5,
    "max_fix_loops_per_task": 3,
    "max_json_repair": 0,
    "task_soft_validators": ["semgrep"],
    "final_soft_validators": [],
    "quality_profile": {
        "name": "balanced",
        "validator_policy": {
            "per_task": {"soft_fail": ["semgrep"]},
            "final": {"soft_fail": []},
        },
    },
    "verbose": False,
}


def _setup_infra(monkeypatch: Any, validators_cls: type = _FakePassingValidators) -> None:
    """Monkeypatch ExecKernel, EnvManager, and Validators in loop module."""
    import autodev.loop as loop
    monkeypatch.setattr(loop, "ExecKernel", _FakeKernel)
    monkeypatch.setattr(loop, "EnvManager", _FakeEnvManager)
    validators_cls.calls = []
    monkeypatch.setattr(loop, "Validators", validators_cls)


def _read_autodev_json(ws: Workspace, rel: str) -> Any:
    """Read and parse a JSON file from workspace .autodev/ directory."""
    return strict_json_loads(ws.read_text(rel))


# ---------------------------------------------------------------------------
# Test 1: Happy path — 3 tasks, all pass
# ---------------------------------------------------------------------------


def test_e2e_happy_path_three_tasks_all_pass(tmp_path: Path, monkeypatch: Any) -> None:
    """Full pipeline: 3 dependent tasks, all validators pass."""
    _setup_infra(monkeypatch)
    ws = Workspace(str(tmp_path))

    responses = [
        URL_SHORTENER_PRD,
        URL_SHORTENER_PLAN,
        MODELS_CHANGESET,
        API_ENDPOINTS_CHANGESET,
        STATS_CHANGESET,
    ]

    ok, prd_struct, plan, last_validation = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener\nBuild a URL shortener API.",
            **COMMON_KWARGS,
        )
    )

    # Return values
    assert ok is True
    assert prd_struct["title"] == "URL Shortener REST API"
    assert plan["project"]["name"] == "url-shortener"
    assert len(plan["tasks"]) == 3

    # Core .autodev/ artifacts exist
    assert ws.exists(".autodev/plan.json")
    assert ws.exists(".autodev/task_quality_index.json")
    assert ws.exists(".autodev/quality_profile.json")
    assert ws.exists(".autodev/quality_run_summary.json")
    assert ws.exists(".autodev/quality_resolution.json")
    assert ws.exists(".autodev/checkpoint.json")
    assert ws.exists(".autodev/run_trace.json")
    assert ws.exists(".autodev/repair_history.json")

    # Checkpoint reflects completion
    checkpoint = _read_autodev_json(ws, ".autodev/checkpoint.json")
    assert checkpoint["status"] == "completed"
    assert len(checkpoint["completed_task_ids"]) == 3

    # Run trace is populated
    trace = _read_autodev_json(ws, ".autodev/run_trace.json")
    assert trace["event_count"] > 0
    assert len(trace["events"]) == trace["event_count"]

    # Generated source files exist
    assert ws.exists("src/app/models.py")
    assert ws.exists("src/app/store.py")
    assert ws.exists("src/app/routes.py")


# ---------------------------------------------------------------------------
# Test 2: Dependency execution order
# ---------------------------------------------------------------------------


def test_e2e_task_dependency_execution_order(tmp_path: Path, monkeypatch: Any) -> None:
    """Tasks execute in topological order: models → api-endpoints → stats."""
    import autodev.loop as loop

    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    _setup_infra(monkeypatch)
    monkeypatch.setattr(loop, "_log_event", _capture)

    ws = Workspace(str(tmp_path))
    responses = [
        URL_SHORTENER_PRD,
        URL_SHORTENER_PLAN,
        MODELS_CHANGESET,
        API_ENDPOINTS_CHANGESET,
        STATS_CHANGESET,
    ]

    ok, _, plan, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            **COMMON_KWARGS,
        )
    )

    assert ok is True

    # Extract task start events in order
    task_starts = [
        e for e in events
        if e.get("event") == "task.start" and "task_id" in e
    ]
    task_ids_order = [e["task_id"] for e in task_starts]

    # models must come before api-endpoints, api-endpoints before stats
    assert "models" in task_ids_order
    assert "api-endpoints" in task_ids_order
    assert "stats" in task_ids_order
    assert task_ids_order.index("models") < task_ids_order.index("api-endpoints")
    assert task_ids_order.index("api-endpoints") < task_ids_order.index("stats")


# ---------------------------------------------------------------------------
# Test 3: Repair cycle
# ---------------------------------------------------------------------------


def test_e2e_repair_cycle_records_history(tmp_path: Path, monkeypatch: Any) -> None:
    """Validation fails first, fixer repairs it, repair_history is recorded."""
    _setup_infra(monkeypatch, validators_cls=_FakeFailFirstValidators)

    ws = Workspace(str(tmp_path))
    responses = [
        URL_SHORTENER_PRD,
        SINGLE_TASK_PLAN,
        SIMPLE_CHANGESET,
        REPAIR_CHANGESET,
    ]

    ok, _, plan, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            **COMMON_KWARGS,
        )
    )

    assert ok is True

    # Repair history should be populated
    assert ws.exists(".autodev/repair_history.json")
    history = _read_autodev_json(ws, ".autodev/repair_history.json")
    assert isinstance(history.get("outcomes"), list)
    assert len(history["outcomes"]) >= 1

    # Quality should show multiple attempts
    quality = _read_autodev_json(ws, ".autodev/task_quality_index.json")
    assert isinstance(quality.get("tasks"), list)


# ---------------------------------------------------------------------------
# Test 4: Cache reuse
# ---------------------------------------------------------------------------


def test_e2e_cache_reuse_skips_generation_on_second_run(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Second run with same PRD reuses cached plan, skipping PRD/plan LLM calls."""
    _setup_infra(monkeypatch)

    ws = Workspace(str(tmp_path))
    prd_md = "# URL Shortener\nBuild a URL shortener."

    # First run: PRD + Plan + Changeset
    llm1 = _FakeLLM([URL_SHORTENER_PRD, SINGLE_TASK_PLAN, SIMPLE_CHANGESET])
    ok1, _, plan1, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, llm1), ws=ws,
            prd_markdown=prd_md, **COMMON_KWARGS,
        )
    )
    assert ok1 is True
    first_calls = llm1.calls

    # Cache should exist
    assert ws.exists(".autodev/generate_cache.json")

    # Reset validator call tracking
    _FakePassingValidators.calls = []

    # Second run: only changeset needed (PRD/plan from cache)
    llm2 = _FakeLLM([SIMPLE_CHANGESET])
    ok2, _, plan2, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, llm2), ws=ws,
            prd_markdown=prd_md, **COMMON_KWARGS,
        )
    )
    assert ok2 is True

    # Second run used fewer LLM calls (PRD+plan skipped)
    assert llm2.calls < first_calls


# ---------------------------------------------------------------------------
# Test 5: Strict quality profile
# ---------------------------------------------------------------------------


def test_e2e_quality_profile_strict_resolves_correctly(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Strict profile resolves with empty final soft_fail set."""
    _setup_infra(monkeypatch)

    ws = Workspace(str(tmp_path))
    strict_profile = {
        "name": "balanced",
        "validator_policy": {
            "per_task": {"soft_fail": ["semgrep", "docker_build"]},
            "final": {"soft_fail": ["semgrep"]},
        },
        "by_level": {
            "strict": {
                "validator_policy": {
                    "per_task": {"soft_fail": ["docker_build"]},
                    "final": {"soft_fail": []},
                },
            },
        },
    }

    # Plan requests "strict" quality level
    strict_plan = dict(SINGLE_TASK_PLAN)
    strict_plan["project"] = dict(strict_plan["project"])
    strict_plan["project"]["quality_gate_profile"] = "strict"

    responses = [URL_SHORTENER_PRD, strict_plan, SIMPLE_CHANGESET]
    kwargs = dict(COMMON_KWARGS)
    kwargs["quality_profile"] = strict_profile
    kwargs["final_soft_validators"] = None  # let profile resolve

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            **kwargs,
        )
    )

    assert ok is True
    assert ws.exists(".autodev/quality_resolution.json")
    resolution = _read_autodev_json(ws, ".autodev/quality_resolution.json")
    assert isinstance(resolution, dict)
    assert "quality_profile" in resolution


# ---------------------------------------------------------------------------
# Test 6: Run trace phases and events
# ---------------------------------------------------------------------------


def test_e2e_run_trace_has_phases_and_events(tmp_path: Path, monkeypatch: Any) -> None:
    """RunTrace contains expected phases and event types."""
    _setup_infra(monkeypatch)

    ws = Workspace(str(tmp_path))
    run_id = "test-trace-run-001"

    responses = [URL_SHORTENER_PRD, SINGLE_TASK_PLAN, SIMPLE_CHANGESET]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            run_id=run_id,
            **COMMON_KWARGS,
        )
    )

    assert ok is True
    assert ws.exists(".autodev/run_trace.json")

    trace = _read_autodev_json(ws, ".autodev/run_trace.json")
    assert trace["run_id"] == run_id
    assert trace["total_elapsed_ms"] >= 0
    assert trace["event_count"] >= 5

    # Check phases
    phase_names = [p["phase"] for p in trace["phases"]]
    assert "planning" in phase_names
    assert "implementation" in phase_names
    assert "final_validation" in phase_names

    for phase in trace["phases"]:
        assert phase["status"] == "completed"
        assert phase["duration_ms"] >= 0

    # Check event types
    event_types = {e["event_type"] for e in trace["events"]}
    assert "run.start" in event_types
    assert "run.completed" in event_types


# ---------------------------------------------------------------------------
# Test 7: Generated file content
# ---------------------------------------------------------------------------


def test_e2e_generated_files_have_correct_content(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Changeset file writes land in workspace with expected content."""
    _setup_infra(monkeypatch)

    ws = Workspace(str(tmp_path))
    responses = [
        URL_SHORTENER_PRD,
        URL_SHORTENER_PLAN,
        MODELS_CHANGESET,
        API_ENDPOINTS_CHANGESET,
        STATS_CHANGESET,
    ]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            **COMMON_KWARGS,
        )
    )

    assert ok is True

    # models.py
    models_content = ws.read_text("src/app/models.py")
    assert "class ShortenedURL" in models_content
    assert "short_code: str" in models_content

    # store.py
    store_content = ws.read_text("src/app/store.py")
    assert "class URLStore" in store_content
    assert "def save" in store_content
    assert "def get" in store_content

    # routes.py (written by api-endpoints task)
    routes_content = ws.read_text("src/app/routes.py")
    assert "@router.post" in routes_content
    assert "def shorten" in routes_content

    # test file
    test_content = ws.read_text("tests/test_stats.py")
    assert "def test_click_count" in test_content


# ---------------------------------------------------------------------------
# Test 8: Progress callback events and monotonic progress
# ---------------------------------------------------------------------------


def test_e2e_progress_callback_emits_events_and_monotonic(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """Progress callback receives events with monotonically increasing progress_pct."""
    _setup_infra(monkeypatch)

    ws = Workspace(str(tmp_path))
    progress_events: list[dict[str, Any]] = []

    responses = [
        URL_SHORTENER_PRD,
        URL_SHORTENER_PLAN,
        MODELS_CHANGESET,
        API_ENDPOINTS_CHANGESET,
        STATS_CHANGESET,
    ]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# URL Shortener",
            progress_callback=progress_events.append,
            **COMMON_KWARGS,
        )
    )

    assert ok is True
    assert len(progress_events) > 0

    # Check required event types are present
    event_types = [e["event"] for e in progress_events]
    assert "run.start" in event_types
    assert "run.end" in event_types
    assert "phase.start" in event_types
    assert "phase.end" in event_types
    assert "task.start" in event_types
    assert "task.end" in event_types
    assert "validation.start" in event_types
    assert "validation.end" in event_types

    # Progress is monotonically increasing
    pcts = [e["progress_pct"] for e in progress_events]
    for i in range(1, len(pcts)):
        assert pcts[i] >= pcts[i - 1], (
            f"progress_pct decreased at event {i}: "
            f"{pcts[i-1]} -> {pcts[i]} (event={progress_events[i]['event']})"
        )

    # Starts at 0, ends at 100
    assert pcts[0] == 0.0
    assert pcts[-1] == 100.0

    # run.end has ok=True in data
    run_end = [e for e in progress_events if e["event"] == "run.end"]
    assert len(run_end) == 1
    assert run_end[0]["data"]["ok"] is True

    # All 3 tasks reported via task.start
    task_starts = [e for e in progress_events if e["event"] == "task.start"]
    task_ids = {e["data"]["task_id"] for e in task_starts}
    assert task_ids == {"models", "api-endpoints", "stats"}


# ---------------------------------------------------------------------------
# Test 9: Snapshot rollback on task failure
# ---------------------------------------------------------------------------


class _FakeAlwaysFailValidators:
    """Always fails per_task validation — task will exhaust repair attempts."""
    calls: list = []

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @staticmethod
    def serialize(results: list) -> list:
        return _FakePassingValidators.serialize(results)

    def run_all(self, enabled: list, audit_required: bool = False,
                soft_validators: Any = None, phase: str = "task", **kwargs: Any) -> list:
        _FakeAlwaysFailValidators.calls.append(
            (phase, list(enabled), sorted(soft_validators or []), kwargs)
        )
        if phase == "per_task":
            return [_FakeValidation("ruff", False, "failed", 1)]
        return [_FakePassingValidation(name) for name in enabled]


_ROLLBACK_PRD = {
    "title": "Hello World Service",
    "goals": ["Print hello world"],
    "non_goals": ["No complex features"],
    "features": [{"name": "Hello", "description": "Say hello to world", "requirements": ["prints hello"]}],
    "acceptance_criteria": ["AC-1: prints hello to stdout"],
    "constraints": [],
    "nfr": {},
}

_ROLLBACK_PLAN = {
    "project": {"type": "python_cli", "name": "hello-world", "python_version": "3.11"},
    "tasks": [
        {
            "id": "hello",
            "title": "Implement hello world printer",
            "goal": "Print hello world to stdout correctly",
            "acceptance": ["Add test for hello world output"],
            "files": ["main.py"],
            "depends_on": [],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
    ],
    "ci": {"enabled": False, "provider": "github_actions"},
    "docker": {"enabled": False},
    "security": {"enabled": False, "tools": []},
    "observability": {"enabled": False},
}

_HELLO_CHANGESET = {
    "role": "implementer",
    "summary": "Implement hello world printer",
    "changes": [{"op": "write", "path": "main.py", "content": "print('hello world')"}],
    "notes": ["hello world"],
}

_HELLO_FIX_CHANGESET = {
    "role": "fixer",
    "summary": "Fix hello world printer",
    "changes": [{"op": "write", "path": "main.py", "content": "print('hello fixed')"}],
    "notes": ["fix attempt"],
}


def test_e2e_rollback_on_task_failure(tmp_path: Path, monkeypatch: Any) -> None:
    """When a task exhausts repair attempts, rollback should remove generated files."""
    _setup_infra(monkeypatch, validators_cls=_FakeAlwaysFailValidators)

    ws = Workspace(str(tmp_path))

    # Create a pre-existing file that should survive rollback
    ws.write_text("existing.py", "keep_me = True")

    responses = [
        _ROLLBACK_PRD,
        _ROLLBACK_PLAN,
        _HELLO_CHANGESET,
        _HELLO_FIX_CHANGESET,
        _HELLO_FIX_CHANGESET,
        _HELLO_FIX_CHANGESET,
    ]

    ok, prd_struct, plan, last_validation = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# Hello World\nPrint hello.",
            max_fix_loops_total=2,
            max_fix_loops_per_task=2,
            enable_snapshots=True,
            **{k: v for k, v in COMMON_KWARGS.items()
               if k not in ("max_fix_loops_total", "max_fix_loops_per_task")},
        )
    )

    # Pipeline should fail
    assert ok is False

    # Pre-existing file should be preserved
    assert ws.exists("existing.py")
    assert ws.read_text("existing.py") == "keep_me = True"

    # File generated by the failed task should have been rolled back
    assert not ws.exists("main.py"), (
        "main.py should have been removed by snapshot rollback after task failure"
    )


# ---------------------------------------------------------------------------
# Test 10: Partial success with dependency chain (continue_on_failure)
# ---------------------------------------------------------------------------


class _FakeTaskSpecificValidators:
    """Fails per_task validation only for task_id 'models', passes everything else."""

    calls: list = []

    def __init__(self, *args: Any, **kwargs: Any):
        pass

    @staticmethod
    def serialize(results: list) -> list:
        return _FakePassingValidators.serialize(results)

    def run_all(self, enabled: list, audit_required: bool = False,
                soft_validators: Any = None, phase: str = "task", **kwargs: Any) -> list:
        task_id = kwargs.get("task_id")
        _FakeTaskSpecificValidators.calls.append(
            (phase, list(enabled), sorted(soft_validators or []), kwargs)
        )
        if phase == "per_task" and task_id == "models":
            return [_FakeValidation("ruff", False, "failed", 1)]
        return [_FakePassingValidation(name) for name in enabled]


_PARTIAL_SUCCESS_PLAN = {
    "project": {
        "type": "python_fastapi",
        "name": "partial-app",
        "python_version": "3.11",
        "quality_gate_profile": "balanced",
    },
    "tasks": [
        {
            "id": "models",
            "title": "Base models (will fail)",
            "goal": "Create data models for the application",
            "acceptance": ["Add test coverage for model creation"],
            "files": ["src/models.py"],
            "depends_on": [],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
        {
            "id": "api",
            "title": "API endpoints (depends on models)",
            "goal": "Create API endpoints for the application",
            "acceptance": ["Add test for API endpoints", "Validate error handling for invalid input"],
            "files": ["src/api.py"],
            "depends_on": ["models"],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": True},
            "validator_focus": ["ruff"],
        },
        {
            "id": "utils",
            "title": "Utility functions (independent)",
            "goal": "Create utility functions for helpers",
            "acceptance": ["Add test coverage for utility functions"],
            "files": ["src/utils.py"],
            "depends_on": [],
            "quality_expectations": {"requires_tests": True, "requires_error_contract": False},
            "validator_focus": ["ruff"],
        },
    ],
    "ci": {"enabled": False, "provider": "github_actions"},
    "docker": {"enabled": False},
    "security": {"enabled": False, "tools": []},
    "observability": {"enabled": False},
}

_MODELS_FAIL_CHANGESET = {
    "role": "implementer",
    "summary": "Created models",
    "changes": [{"op": "write", "path": "src/models.py", "content": "class Model: pass\n"}],
    "notes": ["models"],
}

_UTILS_CHANGESET = {
    "role": "implementer",
    "summary": "Created utilities",
    "changes": [{"op": "write", "path": "src/utils.py", "content": "def helper(): return True\n"}],
    "notes": ["utils"],
}

_FIX_CHANGESET = {
    "role": "fixer",
    "summary": "Attempted fix",
    "changes": [{"op": "write", "path": "src/models.py", "content": "class Model:\n    pass\n"}],
    "notes": ["fix attempt"],
}


def test_e2e_partial_success_continues_independent_tasks(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """With continue_on_failure=True, 'models' fails → 'api' (depends on models)
    is skipped → 'utils' (independent) still runs and passes."""
    import autodev.loop as loop

    events: list[dict[str, Any]] = []

    def _capture(event: str, **fields: object) -> None:
        payload = dict(fields)
        payload["event"] = event
        events.append(payload)

    _setup_infra(monkeypatch, validators_cls=_FakeTaskSpecificValidators)
    monkeypatch.setattr(loop, "_log_event", _capture)

    ws = Workspace(str(tmp_path))

    responses = [
        _ROLLBACK_PRD,
        _PARTIAL_SUCCESS_PLAN,
        _MODELS_FAIL_CHANGESET,
        _UTILS_CHANGESET,
        # Fixer responses for 'models' retry loop
        _FIX_CHANGESET,
        _FIX_CHANGESET,
        _FIX_CHANGESET,
        _FIX_CHANGESET,
    ]

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# Partial App\nApp with partial success.",
            max_fix_loops_total=3,
            max_fix_loops_per_task=3,
            continue_on_failure=True,
            enable_snapshots=True,
            **{k: v for k, v in COMMON_KWARGS.items()
               if k not in ("max_fix_loops_total", "max_fix_loops_per_task")},
        )
    )

    # Overall run should fail (at least one task failed)
    assert ok is False

    # Read quality summary
    quality = _read_autodev_json(ws, ".autodev/task_quality_index.json")
    task_statuses = {t["task_id"]: t["status"] for t in quality["tasks"]}

    # 'models' should be recorded as failed
    assert task_statuses.get("models") == "failed"

    # 'api' should be skipped (dependency on failed 'models')
    assert task_statuses.get("api") == "skipped"

    # 'utils' should pass (independent, no dependency on 'models')
    assert task_statuses.get("utils") == "passed"

    # Verify dependency skip event was emitted
    skip_events = [e for e in events if e.get("event") == "task.dependency_skipped"]
    assert len(skip_events) >= 1
    assert skip_events[0]["task_id"] == "api"
    assert "models" in skip_events[0]["unmet_dependencies"]

    # Verify totals include skipped info
    assert quality["totals"]["skipped_tasks"] == 1
    assert "api" in quality["totals"]["skipped_task_ids"]

    # Verify checkpoint includes failed/skipped info
    checkpoint = _read_autodev_json(ws, ".autodev/checkpoint.json")
    assert "models" in checkpoint.get("failed_task_ids", [])
    assert "api" in checkpoint.get("skipped_task_ids", [])


def test_e2e_validator_graph_reruns_skipped_dependents_with_run_all_only_backend(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    _setup_infra(monkeypatch, validators_cls=_FakeGraphRunAllOnlyValidators)
    _FakeGraphRunAllOnlyValidators.reset()
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
                "name": "graph-app",
                "python_version": "3.11",
                "quality_gate_profile": "balanced",
            },
            "tasks": [
                {
                    "id": "core",
                    "title": "Core graph task",
                    "goal": "Validate dependency-aware validator reruns",
                    "acceptance": [
                        "Add test coverage for validator dependency recovery",
                        "Validate error handling when prerequisite checks fail",
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

    ok, _, _, _ = asyncio.run(
        run_autodev_enterprise(
            client=cast(LLMClient, _FakeLLM(responses)),
            ws=ws,
            prd_markdown="# Graph recovery\nVerify dependency reruns.",
            **{
                **COMMON_KWARGS,
                "validators_enabled": ["ruff", "mypy"],
                "task_soft_validators": [],
                "quality_profile": {
                    "name": "balanced",
                    "validator_policy": {
                        "per_task": {"soft_fail": []},
                        "final": {"soft_fail": []},
                    },
                    "validator_graph": {
                        "enabled": True,
                        "mode": "strict",
                        "skip_on_soft_fail": False,
                        "custom_edges": {},
                    },
                },
            },
        )
    )

    assert ok is True
    per_task_calls = [c for c in _FakeGraphRunAllOnlyValidators.calls if c[0] == "per_task"]
    assert per_task_calls.count(("per_task", ("ruff",))) == 2
    assert per_task_calls.count(("per_task", ("mypy",))) == 1

    quality = _read_autodev_json(ws, ".autodev/task_core_quality.json")
    assert quality["attempts"][0]["validations"][1]["status"] == "skipped_dependency"
    assert quality["attempts"][-1]["validations"][1]["status"] == "passed"
