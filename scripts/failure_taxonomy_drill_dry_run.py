#!/usr/bin/env python3
"""AV5-008 taxonomy drill dry-run artifact generator."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED_LANES = {"auto_fix", "manual", "escalate"}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def run_dry_run(*, example_path: Path, artifacts_dir: Path) -> Path:
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    classes = payload.get("failure_classes") if isinstance(payload.get("failure_classes"), list) else []
    drills = payload.get("drill_examples") if isinstance(payload.get("drill_examples"), list) else []

    class_map = {
        str(item.get("id")): item
        for item in classes
        if isinstance(item, dict) and item.get("id")
    }

    checks: list[dict[str, Any]] = []
    lanes_seen: set[str] = set()
    for row in drills:
        if not isinstance(row, dict):
            continue
        drill_id = str(row.get("id") or "<missing>")
        class_id = str(row.get("failure_class") or "")
        expected_lane = str(row.get("expected_lane") or "")
        klass = class_map.get(class_id)
        if not isinstance(klass, dict):
            raise RuntimeError(f"missing class for drill {drill_id}: {class_id}")

        actual_lane = str(klass.get("remediation_lane") or "")
        ok = actual_lane == expected_lane
        checks.append(
            {
                "id": drill_id,
                "failure_class": class_id,
                "typed_code": row.get("typed_code"),
                "expected_lane": expected_lane,
                "actual_lane": actual_lane,
                "ok": ok,
            }
        )
        if expected_lane in EXPECTED_LANES:
            lanes_seen.add(expected_lane)

    if lanes_seen != EXPECTED_LANES:
        missing = ", ".join(sorted(EXPECTED_LANES - lanes_seen))
        raise RuntimeError(f"drill coverage missing remediation lanes: {missing}")

    failed = [item for item in checks if not item.get("ok")]
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_dir = artifacts_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "schema_version": "av5-008-failure-taxonomy-drill-v1",
        "generated_at": _utc_now(),
        "policy_id": payload.get("policy_id"),
        "ok": len(failed) == 0,
        "total": len(checks),
        "failed": len(failed),
        "lanes_covered": sorted(lanes_seen),
    }

    (out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "checks.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")

    if failed:
        ids = ", ".join(str(item.get("id")) for item in failed)
        raise RuntimeError(f"taxonomy drill mismatches: {ids}")

    return out_dir


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="AV5-008 drill dry-run for failure taxonomy refresh")
    ap.add_argument(
        "--example",
        default="docs/ops/autonomous_failure_taxonomy_v2.example.json",
        help="path to failure taxonomy v2 canonical example JSON",
    )
    ap.add_argument(
        "--artifacts-dir",
        default="artifacts/failure-taxonomy-drill-dry-run",
        help="directory to persist dry-run result/check snapshots",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    example_path = Path(args.example)
    artifacts_dir = Path(args.artifacts_dir)

    if not example_path.exists():
        print(f"[AV5-008 taxonomy drill] FAIL: missing example file {example_path}")
        return 1

    try:
        out_dir = run_dry_run(example_path=example_path, artifacts_dir=artifacts_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[AV5-008 taxonomy drill] FAIL: {exc}")
        print(f"[AV5-008 taxonomy drill] Artifacts root: {artifacts_dir}")
        return 1

    print("[AV5-008 taxonomy drill] PASS")
    print(f"[AV5-008 taxonomy drill] Artifacts: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
