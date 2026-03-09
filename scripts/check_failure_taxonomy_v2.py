#!/usr/bin/env python3
"""Validate AV5 failure taxonomy refresh schema + canonical example."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_RETRYABILITY = {"retryable", "non_retryable"}
EXPECTED_LANES = {"auto_fix", "manual", "escalate"}
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

    required_top = {"$schema", "$id", "title", "type", "required", "properties", "$defs"}
    missing = sorted(required_top - set(schema.keys()))
    if missing:
        errors.append(ValidationError(f"schema missing top-level keys: {missing}"))

    policy_id_const = schema.get("properties", {}).get("policy_id", {}).get("const")
    if policy_id_const != "autonomous.failure-taxonomy.v2":
        errors.append(ValidationError("schema.properties.policy_id.const must be autonomous.failure-taxonomy.v2"))

    retryability_enum = schema.get("$defs", {}).get("retryability", {}).get("enum", [])
    if set(retryability_enum) != EXPECTED_RETRYABILITY:
        errors.append(ValidationError(f"schema retryability enum must be {sorted(EXPECTED_RETRYABILITY)}"))

    lane_enum = schema.get("$defs", {}).get("remediationLane", {}).get("enum", [])
    if set(lane_enum) != EXPECTED_LANES:
        errors.append(ValidationError(f"schema remediationLane enum must be {sorted(EXPECTED_LANES)}"))

    return errors


def _validate_example(example: Any) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if not isinstance(example, dict):
        return [ValidationError("example root must be a JSON object")]

    if example.get("policy_id") != "autonomous.failure-taxonomy.v2":
        errors.append(ValidationError("example.policy_id must be autonomous.failure-taxonomy.v2"))

    version = example.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        errors.append(ValidationError("example.version must match ^v\\d+$"))

    classes = example.get("failure_classes")
    if not isinstance(classes, list) or len(classes) < 5:
        return errors + [ValidationError("example.failure_classes must include at least 5 entries")]

    class_map: dict[str, dict[str, Any]] = {}
    lanes_seen: set[str] = set()
    retryability_seen: set[str] = set()

    for idx, row in enumerate(classes, start=1):
        if not isinstance(row, dict):
            errors.append(ValidationError(f"failure_classes[{idx}] must be an object"))
            continue

        class_id = str(row.get("id") or "")
        if not class_id:
            errors.append(ValidationError(f"failure_classes[{idx}] missing id"))
            continue
        if class_id in class_map:
            errors.append(ValidationError(f"duplicate failure class id: {class_id}"))
            continue

        retryability = str(row.get("retryability") or "")
        lane = str(row.get("remediation_lane") or "")
        families = row.get("code_families")
        rationale = str(row.get("rationale") or "")

        if retryability not in EXPECTED_RETRYABILITY:
            errors.append(ValidationError(f"class[{class_id}].retryability must be one of {sorted(EXPECTED_RETRYABILITY)}"))
        if lane not in EXPECTED_LANES:
            errors.append(ValidationError(f"class[{class_id}].remediation_lane must be one of {sorted(EXPECTED_LANES)}"))
        if not isinstance(families, list) or not families:
            errors.append(ValidationError(f"class[{class_id}].code_families must be a non-empty array"))
        if len(rationale) < 10:
            errors.append(ValidationError(f"class[{class_id}].rationale must be at least 10 characters"))

        if retryability in EXPECTED_RETRYABILITY:
            retryability_seen.add(retryability)
        if lane in EXPECTED_LANES:
            lanes_seen.add(lane)

        class_map[class_id] = row

    if retryability_seen != EXPECTED_RETRYABILITY:
        errors.append(ValidationError(f"failure_classes must cover retryability split: {sorted(EXPECTED_RETRYABILITY)}"))

    if lanes_seen != EXPECTED_LANES:
        errors.append(ValidationError(f"failure_classes must cover remediation lanes: {sorted(EXPECTED_LANES)}"))

    drills = example.get("drill_examples")
    if not isinstance(drills, list) or not drills:
        return errors + [ValidationError("example.drill_examples must be a non-empty array")]

    for idx, row in enumerate(drills, start=1):
        if not isinstance(row, dict):
            errors.append(ValidationError(f"drill_examples[{idx}] must be an object"))
            continue
        drill_id = str(row.get("id") or f"#{idx}")
        class_id = str(row.get("failure_class") or "")
        expected_lane = str(row.get("expected_lane") or "")

        klass = class_map.get(class_id)
        if klass is None:
            errors.append(ValidationError(f"drill[{drill_id}] references unknown failure_class {class_id!r}"))
            continue

        default_lane = str(klass.get("remediation_lane") or "")
        if expected_lane not in EXPECTED_LANES:
            errors.append(ValidationError(f"drill[{drill_id}] expected_lane must be one of {sorted(EXPECTED_LANES)}"))
            continue

        if expected_lane != default_lane:
            errors.append(
                ValidationError(
                    f"drill[{drill_id}] lane mismatch: expected_lane={expected_lane} but class default is {default_lane}"
                )
            )

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate autonomous failure taxonomy v2 schema/example")
    ap.add_argument(
        "--schema",
        default="docs/ops/autonomous_failure_taxonomy_v2.schema.json",
        help="Path to failure taxonomy v2 JSON schema",
    )
    ap.add_argument(
        "--example",
        default="docs/ops/autonomous_failure_taxonomy_v2.example.json",
        help="Path to canonical failure taxonomy v2 example",
    )
    args = ap.parse_args()

    schema_path = Path(args.schema)
    example_path = Path(args.example)

    missing = [str(p) for p in (schema_path, example_path) if not p.exists()]
    if missing:
        print("[FAIL] missing required file(s):")
        for path in missing:
            print(f"  - {path}")
        return 1

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

    errors = _validate_schema(schema)
    errors.extend(_validate_example(example))

    if errors:
        print("[FAIL] failure taxonomy v2 validation failed")
        for err in errors:
            print(f"  - {err.message}")
        return 1

    print("[PASS] failure taxonomy v2 schema/example validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
