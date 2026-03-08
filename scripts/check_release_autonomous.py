#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autodev.autonomous_evidence_schema import (  # noqa: E402
    AUTONOMOUS_EVIDENCE_SCHEMA_VERSION,
    validate_schema_payload,
)

DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "autonomous-e2e-smoke"


class ValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValidationError([f"missing required file: {path}"]) from None
    except json.JSONDecodeError as exc:
        raise ValidationError([f"invalid JSON in {path}: {exc}"]) from None


def _latest_run_dir(root: Path) -> Path:
    if not root.exists() or not root.is_dir():
        raise ValidationError([f"artifacts directory does not exist: {root}"])

    run_dirs = sorted([p for p in root.iterdir() if p.is_dir()])
    if not run_dirs:
        raise ValidationError([f"no smoke runs found under: {root}"])
    return run_dirs[-1]


def _expect_dict(value: Any, *, path: str, errors: list[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    errors.append(f"{path} must be an object")
    return {}


def _expect_non_empty(value: Any, *, path: str, errors: list[str]) -> None:
    if value in (None, "", [], {}):
        errors.append(f"{path} is missing or empty")


def _validate_schema(
    *,
    artifacts: dict[str, tuple[str, Any]],
    tolerant_legacy: bool,
    errors: list[str],
    warnings: list[str],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name, (path, value) in artifacts.items():
        if not isinstance(value, dict):
            if tolerant_legacy and name in ("report", "strategy"):
                warnings.append(
                    f"legacy compatibility mode: optional {path} missing; accepted for AV2 artifacts"
                )
                versions[name] = "av2-legacy"
                continue
            errors.append(f"{path} must be an object")
            continue

        version, diagnostics = validate_schema_payload(
            name=name,
            payload=value,
            path=path,
            tolerant_legacy=tolerant_legacy,
        )
        versions[name] = version
        for item in diagnostics:
            if item.level == "warning":
                warnings.append(item.message)
            else:
                errors.append(item.message)

    return versions


def validate(run_dir: Path, *, tolerant_legacy: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    result = _load_json(run_dir / "result.json")
    snapshots = _load_json(run_dir / "snapshots.json")

    result_obj = _expect_dict(result, path="result.json", errors=errors)
    snapshots_obj = _expect_dict(snapshots, path="snapshots.json", errors=errors)

    if result_obj.get("ok") is not True:
        errors.append("result.json.ok must be true")

    state = _expect_dict(snapshots_obj.get("state"), path="snapshots.state", errors=errors)
    preflight = _expect_dict(state.get("preflight"), path="snapshots.state.preflight", errors=errors)
    if preflight.get("status") != "passed":
        errors.append("preflight signal missing: snapshots.state.preflight.status must be 'passed'")

    gate_results = _expect_dict(snapshots_obj.get("gate_results"), path="snapshots.gate_results", errors=errors)
    attempts = gate_results.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        errors.append("gate signal missing: snapshots.gate_results.attempts must be a non-empty list")

    guard = _expect_dict(snapshots_obj.get("guard"), path="snapshots.guard", errors=errors)
    latest_guard = _expect_dict(guard.get("latest"), path="snapshots.guard.latest", errors=errors)
    _expect_non_empty(latest_guard.get("reason_code"), path="snapshots.guard.latest.reason_code", errors=errors)

    summary = _expect_dict(snapshots_obj.get("summary_json"), path="snapshots.summary_json", errors=errors)
    if summary.get("preflight_status") != "passed":
        errors.append("summary signal mismatch: snapshots.summary_json.preflight_status must be 'passed'")

    gate_counts = _expect_dict(summary.get("gate_counts"), path="snapshots.summary_json.gate_counts", errors=errors)
    if not isinstance(gate_counts.get("total"), int) or gate_counts.get("total", 0) < 1:
        errors.append("summary signal missing: snapshots.summary_json.gate_counts.total must be >= 1")

    summary_guard = _expect_dict(summary.get("guard_decision"), path="snapshots.summary_json.guard_decision", errors=errors)
    _expect_non_empty(summary_guard.get("reason_code"), path="snapshots.summary_json.guard_decision.reason_code", errors=errors)

    api_smoke = _expect_dict(
        snapshots_obj.get("quality_gate_latest"),
        path="snapshots.quality_gate_latest",
        errors=errors,
    )
    if api_smoke.get("empty") is True:
        errors.append("api smoke evidence missing: snapshots.quality_gate_latest.empty must be false")

    api_summary = _expect_dict(api_smoke.get("summary"), path="snapshots.quality_gate_latest.summary", errors=errors)
    if api_summary.get("preflight_status") != "passed":
        errors.append("api smoke summary mismatch: preflight_status must be 'passed'")

    api_guard = _expect_dict(api_summary.get("guard_decision"), path="snapshots.quality_gate_latest.summary.guard_decision", errors=errors)
    _expect_non_empty(
        api_guard.get("reason_code"),
        path="snapshots.quality_gate_latest.summary.guard_decision.reason_code",
        errors=errors,
    )

    schema_versions = _validate_schema(
        artifacts={
            "report": ("snapshots.report", snapshots_obj.get("report")),
            "gate": ("snapshots.gate_results", gate_results),
            "strategy": ("snapshots.strategy_trace", snapshots_obj.get("strategy_trace")),
            "guard": ("snapshots.guard", guard),
            "preflight": ("snapshots.state.preflight", preflight),
            "summary_snapshot": ("snapshots.summary_json", summary),
        },
        tolerant_legacy=tolerant_legacy,
        errors=errors,
        warnings=warnings,
    )

    if errors:
        raise ValidationError(errors)

    return {
        "ok": True,
        "run_dir": str(run_dir),
        "warnings": warnings,
        "schema_versions": schema_versions,
        "signals": {
            "preflight": preflight.get("status"),
            "gate_attempts": len(attempts),
            "guard_reason_code": latest_guard.get("reason_code"),
            "summary_guard_reason_code": summary_guard.get("reason_code"),
            "api_guard_reason_code": api_guard.get("reason_code"),
        },
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="AV3-002 autonomous release evidence checker")
    ap.add_argument(
        "--artifacts-dir",
        default=str(DEFAULT_ARTIFACTS_DIR),
        help="autonomous e2e smoke artifacts directory (default: ./artifacts/autonomous-e2e-smoke)",
    )
    ap.add_argument(
        "--run-dir",
        default="",
        help="specific smoke run directory to verify (defaults to latest under --artifacts-dir)",
    )
    ap.add_argument(
        "--strict-schema",
        action="store_true",
        help=(
            "require declared schema_version for all evidence artifacts "
            f"(expected {AUTONOMOUS_EVIDENCE_SCHEMA_VERSION})"
        ),
    )
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else _latest_run_dir(Path(args.artifacts_dir).expanduser().resolve())

    try:
        summary = validate(run_dir, tolerant_legacy=not bool(args.strict_schema))
    except ValidationError as exc:
        if args.json:
            print(json.dumps({"ok": False, "run_dir": str(run_dir), "errors": exc.errors}, ensure_ascii=False, indent=2))
        else:
            print("[AV3-002 release check] FAIL")
            print(f"run_dir: {run_dir}")
            for idx, error in enumerate(exc.errors, start=1):
                print(f"  {idx}. {error}")
        return 1

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("[AV3-002 release check] PASS")
        print(f"run_dir: {summary['run_dir']}")
        print(f"guard_reason_code: {summary['signals']['guard_reason_code']}")
        if summary.get("warnings"):
            print("warnings:")
            for warning in summary["warnings"]:
                print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
