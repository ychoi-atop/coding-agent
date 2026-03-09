#!/usr/bin/env python3
"""Render/check AV5 kickoff smoke evidence index (AV5-013)."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
STAMP_FORMAT = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class SmokeSource:
    ticket: str
    check: str
    artifacts_dir: Path


SOURCES: tuple[SmokeSource, ...] = (
    SmokeSource(
        ticket="AV2-013",
        check="autonomous_e2e_smoke",
        artifacts_dir=REPO_ROOT / "artifacts" / "autonomous-e2e-smoke",
    ),
    SmokeSource(
        ticket="AV5-004",
        check="retry_strategy_replay_smoke",
        artifacts_dir=REPO_ROOT / "artifacts" / "retry-strategy-replay-smoke",
    ),
    SmokeSource(
        ticket="AV5-008",
        check="failure_taxonomy_drill_dry_run",
        artifacts_dir=REPO_ROOT / "artifacts" / "failure-taxonomy-drill-dry-run",
    ),
)


def _parse_stamp(stamp: str) -> datetime:
    return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=timezone.utc)


def _latest_run_dir(artifacts_dir: Path) -> Path:
    run_dirs = sorted(p for p in artifacts_dir.iterdir() if p.is_dir())
    if not run_dirs:
        raise RuntimeError(f"no smoke runs found under {artifacts_dir}")
    return run_dirs[-1]


def _read_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing result file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in result file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"result file must contain a JSON object: {path}")
    return payload


def _status_label(payload: dict[str, Any]) -> str:
    ok = payload.get("ok")
    if ok is True:
        return "✅ PASS"
    if ok is False:
        return "❌ FAIL"
    return "⚠️ UNKNOWN"


def collect_rows(*, now: datetime, freshness_hours: float) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    freshness_cutoff = now - timedelta(hours=freshness_hours)

    for source in SOURCES:
        if not source.artifacts_dir.exists() or not source.artifacts_dir.is_dir():
            raise RuntimeError(f"artifacts directory does not exist: {source.artifacts_dir}")

        run_dir = _latest_run_dir(source.artifacts_dir)
        stamp = run_dir.name
        try:
            run_time = _parse_stamp(stamp)
        except ValueError as exc:
            raise RuntimeError(f"unexpected run directory name (expected {STAMP_FORMAT}): {run_dir}") from exc

        if run_time < freshness_cutoff:
            raise RuntimeError(
                f"stale smoke evidence for {source.ticket} ({source.check}): "
                f"latest={stamp} cutoff={freshness_cutoff.strftime(STAMP_FORMAT)}"
            )

        result_path = run_dir / "result.json"
        result = _read_result(result_path)

        rows.append(
            {
                "timestamp_kst": run_time.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                "run_time_utc": run_time.isoformat(),
                "source": source.ticket,
                "check": source.check,
                "outcome": _status_label(result),
                "artifact": str(result_path.relative_to(REPO_ROOT)),
            }
        )

    return rows


def render_markdown(*, rows: list[dict[str, str]]) -> str:
    latest_run_time = max(datetime.fromisoformat(row["run_time_utc"]) for row in rows)
    generated_at = latest_run_time.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")

    lines = [
        "# AV5 Kickoff Smoke Evidence Index (AV5-013)",
        "",
        "Status: ✅ Updated",
        f"Generated: {generated_at}",
        "",
        "| Timestamp (KST) | Source | Check | Outcome | Artifact |",
        "|---|---|---|---|---|",
    ]

    for row in rows:
        lines.append(
            f"| {row['timestamp_kst']} | `{row['source']}` | `{row['check']}` | {row['outcome']} | `{row['artifact']}` |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Sources are indexed from the latest timestamped run directory for each kickoff smoke lane.",
            "- Freshness is enforced by `scripts/render_av5_kickoff_smoke_evidence_index.py` via `--freshness-hours`.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render/check AV5 kickoff smoke evidence index")
    p.add_argument(
        "--output",
        default=str(REPO_ROOT / "docs" / "AUTONOMOUS_V5_KICKOFF_SMOKE_EVIDENCE_INDEX.md"),
        help="output markdown path",
    )
    p.add_argument(
        "--freshness-hours",
        type=float,
        default=24.0 * 14,
        help="max age window in hours for each smoke source (default: 336h / 14 days)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="check mode: validate freshness/content only (no file write)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = datetime.now(timezone.utc)

    try:
        rows = collect_rows(now=now, freshness_hours=float(args.freshness_hours))
        rendered = render_markdown(rows=rows)
    except RuntimeError as exc:
        print(f"[AV5-013 smoke index] FAIL: {exc}")
        return 1

    output_path = Path(args.output).expanduser().resolve()
    if args.check:
        existing = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        if existing != rendered:
            print(f"[AV5-013 smoke index] FAIL: stale index file (run renderer): {output_path}")
            return 1
        print(f"[AV5-013 smoke index] PASS (fresh): {output_path}")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"[AV5-013 smoke index] PASS: wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
