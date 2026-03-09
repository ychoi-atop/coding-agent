#!/usr/bin/env python3
"""Validate AV5 stage-boundary contract schema + canonical example.

This script intentionally avoids external deps (e.g., jsonschema) and enforces
repository-specific invariants needed by AV5-003.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_STAGE_ORDER = ["ingest", "plan", "execute", "verify"]
EXPECTED_RETRY_CLASSES = {"retryable", "conditional", "non_retryable"}
VERSION_RE = re.compile(r"^v\d+$")


@dataclass
class ValidationError:
    message: str


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(schema: Any) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if not isinstance(schema, dict):
        return [ValidationError("schema root must be a JSON object")]

    required_top_keys = {"$schema", "$id", "title", "type", "required", "properties", "$defs"}
    missing_top = sorted(required_top_keys - set(schema.keys()))
    if missing_top:
        errors.append(ValidationError(f"schema missing top-level keys: {missing_top}"))

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        errors.append(ValidationError("schema.properties must be an object"))
    else:
        contract_id = properties.get("contract_id", {})
        if contract_id.get("const") != "autonomous.stage-boundary.v1":
            errors.append(ValidationError("schema.properties.contract_id.const must be autonomous.stage-boundary.v1"))

        stages = properties.get("stages", {})
        if stages.get("minItems") != 4 or stages.get("maxItems") != 4:
            errors.append(ValidationError("schema.properties.stages must fix minItems=maxItems=4"))

    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        errors.append(ValidationError("schema.$defs must be an object"))
    else:
        stage_contract = defs.get("stageContract", {})
        if stage_contract.get("type") != "object":
            errors.append(ValidationError("schema.$defs.stageContract.type must be object"))
        name_enum = (
            stage_contract.get("properties", {})
            .get("name", {})
            .get("enum", [])
        )
        if name_enum != EXPECTED_STAGE_ORDER:
            errors.append(ValidationError(f"schema stage name enum must be {EXPECTED_STAGE_ORDER}"))

        failure_semantics = defs.get("failureSemantics", {})
        retry_enum = (
            failure_semantics.get("properties", {})
            .get("retry_class", {})
            .get("enum", [])
        )
        if set(retry_enum) != EXPECTED_RETRY_CLASSES:
            errors.append(ValidationError(f"schema retry_class enum must be {sorted(EXPECTED_RETRY_CLASSES)}"))

    return errors


def _validate_example(example: Any) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if not isinstance(example, dict):
        return [ValidationError("example root must be a JSON object")]

    if example.get("contract_id") != "autonomous.stage-boundary.v1":
        errors.append(ValidationError("example.contract_id must be autonomous.stage-boundary.v1"))

    version = example.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        errors.append(ValidationError("example.version must match ^v\\d+$"))

    stages = example.get("stages")
    if not isinstance(stages, list):
        return errors + [ValidationError("example.stages must be an array")]

    if len(stages) != 4:
        errors.append(ValidationError("example.stages must include exactly 4 stage objects"))
        return errors

    names = [stage.get("name") for stage in stages if isinstance(stage, dict)]
    if names != EXPECTED_STAGE_ORDER:
        errors.append(ValidationError(f"example stage order must be {EXPECTED_STAGE_ORDER}"))

    for stage in stages:
        if not isinstance(stage, dict):
            errors.append(ValidationError("each stage entry must be an object"))
            continue

        name = stage.get("name", "<missing>")
        for field in ("required_inputs", "required_outputs"):
            value = stage.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                errors.append(ValidationError(f"stage[{name}].{field} must be a non-empty array of non-empty strings"))

        failure_semantics = stage.get("failure_semantics")
        if not isinstance(failure_semantics, dict):
            errors.append(ValidationError(f"stage[{name}].failure_semantics must be an object"))
            continue

        retry_class = failure_semantics.get("retry_class")
        if retry_class not in EXPECTED_RETRY_CLASSES:
            errors.append(
                ValidationError(
                    f"stage[{name}].failure_semantics.retry_class must be one of {sorted(EXPECTED_RETRY_CLASSES)}"
                )
            )

        for field in ("stop_condition", "escalate_condition"):
            value = failure_semantics.get(field)
            if not isinstance(value, str) or len(value.strip()) < 5:
                errors.append(ValidationError(f"stage[{name}].failure_semantics.{field} must be a non-empty sentence"))

        evidence = failure_semantics.get("evidence_required")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(v, str) and v for v in evidence):
            errors.append(
                ValidationError(
                    f"stage[{name}].failure_semantics.evidence_required must be a non-empty array of non-empty strings"
                )
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate autonomous stage-boundary schema/example contract")
    parser.add_argument(
        "--schema",
        default="docs/ops/autonomous_stage_boundary_contract.schema.json",
        help="Path to stage-boundary JSON schema",
    )
    parser.add_argument(
        "--example",
        default="docs/ops/autonomous_stage_boundary_contract.example.json",
        help="Path to canonical stage-boundary contract example",
    )
    args = parser.parse_args()

    schema_path = Path(args.schema)
    example_path = Path(args.example)

    missing = [str(p) for p in (schema_path, example_path) if not p.exists()]
    if missing:
        print("[FAIL] missing required file(s):")
        for path in missing:
            print(f"  - {path}")
        return 1

    errors: list[ValidationError] = []

    try:
        schema = _load_json(schema_path)
    except json.JSONDecodeError as exc:
        print(f"[FAIL] invalid JSON in schema file {schema_path}: {exc}")
        return 1

    try:
        example = _load_json(example_path)
    except json.JSONDecodeError as exc:
        print(f"[FAIL] invalid JSON in example file {example_path}: {exc}")
        return 1

    errors.extend(_validate_schema(schema))
    errors.extend(_validate_example(example))

    if errors:
        print("[FAIL] stage-boundary contract validation failed")
        for err in errors:
            print(f"  - {err.message}")
        return 1

    print("[PASS] stage-boundary contract schema/example validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
