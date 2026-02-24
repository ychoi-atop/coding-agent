from jsonschema import validate  # type: ignore[import-untyped]
from autodev.schemas import PLAN_SCHEMA, CHANGESET_SCHEMA


def test_plan_schema_rejects_task_without_quality_expectations():
    invalid = {
        "project": {
            "type": "python_fastapi",
            "name": "x",
            "python_version": "3.11",
        },
        "tasks": [
            {
                "id": "task1",
                "title": "Build core API",
                "goal": "Build core API route",
                "acceptance": ["Add tests"],
                "files": ["src/app/main.py"],
                "depends_on": [],
                "validator_focus": ["ruff"],
            }
        ],
        "ci": {"enabled": True, "provider": "github_actions"},
        "docker": {"enabled": True},
        "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
        "observability": {"enabled": True},
    }

    try:
        validate(instance=invalid, schema=PLAN_SCHEMA)
    except Exception:
        pass
    else:
        assert False, "Expected PLAN_SCHEMA validation error"


def test_plan_schema_rejects_invalid_validator_focus():
    invalid = {
        "project": {
            "type": "python_fastapi",
            "name": "x",
            "python_version": "3.11",
        },
        "tasks": [
            {
                "id": "task1",
                "title": "Build core API",
                "goal": "Build core API route and tests",
                "acceptance": ["Add tests", "Handle typed input"],
                "files": ["src/app/main.py"],
                "depends_on": [],
                "quality_expectations": {
                    "requires_tests": True,
                    "requires_error_contract": True,
                    "touches_contract": True,
                },
                "validator_focus": ["not_a_validator"],
            }
        ],
        "ci": {"enabled": True, "provider": "github_actions"},
        "docker": {"enabled": True},
        "security": {"enabled": True, "tools": ["pip_audit", "bandit", "semgrep"]},
        "observability": {"enabled": True},
    }

    try:
        validate(instance=invalid, schema=PLAN_SCHEMA)
    except Exception:
        pass
    else:
        assert False, "Expected PLAN_SCHEMA validation error"


def test_plan_schema_rejects_quality_rich_acceptance_mismatch():
    invalid = {
        "project": {
            "type": "python_fastapi",
            "name": "x",
            "python_version": "3.11",
            "quality_gate_profile": "strict",
        },
        "tasks": [
            {
                "id": "task1",
                "title": "Build core API",
                "goal": "Build core API route and tests",
                "acceptance": [
                    "Update docs",
                    "Refactor import structure",
                ],
                "files": ["src/app/main.py", "tests/test_api_contract.py"],
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
    }

    try:
        validate(instance=invalid, schema=PLAN_SCHEMA)
    except Exception:
        pass
    else:
        assert False, "Expected PLAN_SCHEMA validation error"


def test_plan_schema_accepts_strict_quality_fields():
    valid = {
        "project": {
            "type": "python_fastapi",
            "name": "x",
            "python_version": "3.11",
            "quality_level": "balanced",
            "quality_gate_profile": "strict",
        },
        "tasks": [
            {
                "id": "task1",
                "title": "Build core API",
                "goal": "Build core API route",
                "acceptance": ["Add unit tests for API", "Handle validation and error response paths"],
                "files": ["src/app/main.py", "tests/test_api_contract.py"],
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
    }

    validate(instance=valid, schema=PLAN_SCHEMA)


def test_changeset_schema_keeps_quality_links_metadata_required_when_present():
    payload = {
        "role": "fixer",
        "summary": "Fixing lint",
        "changes": [
            {"op": "write", "path": "README.md", "content": "updated"}
        ],
        "notes": ["note-1"],
        "quality_notes": ["validator coverage updated"],
        "validation_links": {
            "acceptance": ["AC-1"],
            "tasks": ["task1"],
            "validators": ["ruff", "mypy"],
        },
    }

    validate(instance=payload, schema=CHANGESET_SCHEMA)


def test_changeset_schema_rejects_unknown_validator_link():
    payload = {
        "role": "fixer",
        "summary": "Fixing lint",
        "changes": [],
        "notes": ["note-1"],
        "validation_links": {
            "acceptance": ["AC-1"],
            "tasks": ["task1"],
            "validators": ["unknown"],
        },
    }

    try:
        validate(instance=payload, schema=CHANGESET_SCHEMA)
    except Exception:
        pass
    else:
        assert False, "Expected CHANGESET_SCHEMA validation error"
