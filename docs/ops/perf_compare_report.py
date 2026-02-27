#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRD = ROOT_DIR / "docs" / "ops" / "benchmark_smoke_prd.md"
DEFAULT_OUT_ROOT = ROOT_DIR / "artifacts" / "perf"


@dataclass
class RunMetric:
    lane: str
    repeat: int
    ok: bool
    returncode: int
    wall_time_ms: int
    peak_rss_kb: int | None
    validator_total_ms: int
    validator_max_ms: int
    llm_total_tokens: int
    llm_prompt_tokens: int
    llm_completion_tokens: int
    llm_chat_calls: int
    llm_transport_retries: int
    llm_failed_chat_calls: int
    task_retry_like_count: int
    retries_total: int
    run_dir: str
    error: str = ""


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML object: {path}")
    return data


def _dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def _extract_out_dir(text: str) -> str:
    match = re.search(r"'out':\s*'([^']+)'", text)
    if not match:
        match = re.search(r'"out"\s*:\s*"([^\"]+)"', text)
    return match.group(1) if match else ""


def _extract_peak_rss_kb(stderr: str) -> int | None:
    # macOS /usr/bin/time -l: ru_maxrss is bytes, convert to KB.
    m = re.search(r"(\d+)\s+maximum resident set size", stderr)
    if m:
        rss_bytes = _safe_int(m.group(1))
        return int(rss_bytes / 1024)
    # GNU time -v fallback: already kbytes.
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", stderr)
    if m:
        return _safe_int(m.group(1))
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _collect_validator_metrics(run_dir: Path) -> tuple[int, int, int]:
    task_index = _read_json(run_dir / ".autodev" / "task_quality_index.json")
    total_validation_ms = 0
    max_validation_ms = 0

    tasks = task_index.get("tasks", []) if isinstance(task_index, dict) else []
    if isinstance(tasks, list):
        for task in tasks:
            if not isinstance(task, dict):
                continue
            trend = task.get("attempt_trend", [])
            if isinstance(trend, list):
                for row in trend:
                    if not isinstance(row, dict):
                        continue
                    ms = _safe_int(row.get("duration_ms", 0))
                    total_validation_ms += ms
                    max_validation_ms = max(max_validation_ms, ms)

    final = task_index.get("final", {}) if isinstance(task_index, dict) else {}
    validations = final.get("validations", []) if isinstance(final, dict) else []
    if isinstance(validations, list):
        for row in validations:
            if not isinstance(row, dict):
                continue
            ms = _safe_int(row.get("duration_ms", 0))
            total_validation_ms += ms
            max_validation_ms = max(max_validation_ms, ms)

    totals = task_index.get("totals", {}) if isinstance(task_index, dict) else {}
    task_retry_like_count = max(0, _safe_int(totals.get("total_task_attempts", 0)) - _safe_int(totals.get("tasks", 0)))

    return total_validation_ms, max_validation_ms, task_retry_like_count


def _collect_llm_usage(run_dir: Path) -> dict[str, int]:
    run_meta = _read_json(run_dir / ".autodev" / "run_metadata.json")
    usage = run_meta.get("llm_usage", {}) if isinstance(run_meta, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "prompt_tokens": _safe_int(usage.get("prompt_tokens", 0)),
        "completion_tokens": _safe_int(usage.get("completion_tokens", 0)),
        "total_tokens": _safe_int(usage.get("total_tokens", 0)),
        "chat_calls": _safe_int(usage.get("chat_calls", 0)),
        "transport_retries": _safe_int(usage.get("transport_retries", 0)),
        "failed_chat_calls": _safe_int(usage.get("failed_chat_calls", 0)),
    }


def _run_once(
    lane: str,
    repeat_idx: int,
    prd: Path,
    profile: str,
    out_root: Path,
    cfg_path: Path,
    timeout_sec: int,
) -> RunMetric:
    lane_out = out_root / lane
    lane_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "autodev.main",
        "--prd",
        str(prd),
        "--out",
        str(lane_out),
        "--config",
        str(cfg_path),
        "--profile",
        profile,
    ]

    wrapped_cmd: list[str]
    if Path("/usr/bin/time").exists():
        wrapped_cmd = ["/usr/bin/time", "-l", *cmd]
    else:
        wrapped_cmd = cmd

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            wrapped_cmd,
            cwd=str(ROOT_DIR),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        wall_time_ms = int((time.perf_counter() - t0) * 1000)
    except subprocess.TimeoutExpired as exc:
        return RunMetric(
            lane=lane,
            repeat=repeat_idx,
            ok=False,
            returncode=124,
            wall_time_ms=int((time.perf_counter() - t0) * 1000),
            peak_rss_kb=None,
            validator_total_ms=0,
            validator_max_ms=0,
            llm_total_tokens=0,
            llm_prompt_tokens=0,
            llm_completion_tokens=0,
            llm_chat_calls=0,
            llm_transport_retries=0,
            llm_failed_chat_calls=0,
            task_retry_like_count=0,
            retries_total=0,
            run_dir="",
            error=f"timeout after {timeout_sec}s: {exc}",
        )

    combined = f"{proc.stdout}\n{proc.stderr}"
    run_dir_raw = _extract_out_dir(combined)
    run_dir = Path(run_dir_raw) if run_dir_raw else Path()

    validator_total_ms = 0
    validator_max_ms = 0
    task_retry_like_count = 0
    llm_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "chat_calls": 0,
        "transport_retries": 0,
        "failed_chat_calls": 0,
    }
    if run_dir_raw and run_dir.exists():
        validator_total_ms, validator_max_ms, task_retry_like_count = _collect_validator_metrics(run_dir)
        llm_usage = _collect_llm_usage(run_dir)

    retries_total = llm_usage["transport_retries"] + task_retry_like_count

    return RunMetric(
        lane=lane,
        repeat=repeat_idx,
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        wall_time_ms=wall_time_ms,
        peak_rss_kb=_extract_peak_rss_kb(proc.stderr),
        validator_total_ms=validator_total_ms,
        validator_max_ms=validator_max_ms,
        llm_total_tokens=llm_usage["total_tokens"],
        llm_prompt_tokens=llm_usage["prompt_tokens"],
        llm_completion_tokens=llm_usage["completion_tokens"],
        llm_chat_calls=llm_usage["chat_calls"],
        llm_transport_retries=llm_usage["transport_retries"],
        llm_failed_chat_calls=llm_usage["failed_chat_calls"],
        task_retry_like_count=task_retry_like_count,
        retries_total=retries_total,
        run_dir=run_dir_raw,
        error="" if proc.returncode == 0 else combined[:500],
    )


def _avg(values: list[int | None]) -> float:
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    return float(sum(vals) / len(vals))


def _lane_summary(rows: list[RunMetric]) -> dict[str, float]:
    return {
        "wall_time_ms_avg": _avg([r.wall_time_ms for r in rows]),
        "peak_rss_kb_avg": _avg([r.peak_rss_kb for r in rows]),
        "validator_total_ms_avg": _avg([r.validator_total_ms for r in rows]),
        "validator_max_ms_avg": _avg([r.validator_max_ms for r in rows]),
        "llm_total_tokens_avg": _avg([r.llm_total_tokens for r in rows]),
        "retries_total_avg": _avg([r.retries_total for r in rows]),
        "pass_rate": _avg([1 if r.ok else 0 for r in rows]),
    }


def _write_csv(path: Path, rows: list[RunMetric]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(rows[0]).keys()) if rows else [
        "lane",
        "repeat",
        "ok",
        "returncode",
        "wall_time_ms",
        "peak_rss_kb",
        "validator_total_ms",
        "validator_max_ms",
        "llm_total_tokens",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "llm_chat_calls",
        "llm_transport_retries",
        "llm_failed_chat_calls",
        "task_retry_like_count",
        "retries_total",
        "run_dir",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _delta(after: float, before: float) -> str:
    d = after - before
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}"


def _write_markdown(path: Path, before_rows: list[RunMetric], after_rows: list[RunMetric], args: argparse.Namespace, selected_profile: str) -> None:
    before = _lane_summary(before_rows)
    after = _lane_summary(after_rows)

    lines = [
        "# 성능 비교 리포트 (before vs after)",
        "",
        f"- 생성 시각: {datetime.now().isoformat(timespec='seconds')}",
        f"- PRD: `{args.prd}`",
        f"- profile: `{selected_profile}`",
        f"- 반복 횟수: before={len(before_rows)}, after={len(after_rows)}",
        f"- smoke 모드: `{args.smoke}`",
        "",
        "## 평균 지표",
        "",
        "| Metric | Before(avg) | After(avg) | Delta(after-before) |",
        "|---|---:|---:|---:|",
        f"| wall time (ms) | {before['wall_time_ms_avg']:.1f} | {after['wall_time_ms_avg']:.1f} | {_delta(after['wall_time_ms_avg'], before['wall_time_ms_avg'])} |",
        f"| peak RSS (KB) | {before['peak_rss_kb_avg']:.1f} | {after['peak_rss_kb_avg']:.1f} | {_delta(after['peak_rss_kb_avg'], before['peak_rss_kb_avg'])} |",
        f"| validator total (ms) | {before['validator_total_ms_avg']:.1f} | {after['validator_total_ms_avg']:.1f} | {_delta(after['validator_total_ms_avg'], before['validator_total_ms_avg'])} |",
        f"| validator max (ms) | {before['validator_max_ms_avg']:.1f} | {after['validator_max_ms_avg']:.1f} | {_delta(after['validator_max_ms_avg'], before['validator_max_ms_avg'])} |",
        f"| llm total tokens | {before['llm_total_tokens_avg']:.1f} | {after['llm_total_tokens_avg']:.1f} | {_delta(after['llm_total_tokens_avg'], before['llm_total_tokens_avg'])} |",
        f"| retries total | {before['retries_total_avg']:.1f} | {after['retries_total_avg']:.1f} | {_delta(after['retries_total_avg'], before['retries_total_avg'])} |",
        "",
        "## 핵심 이벤트 정의",
        "",
        "- `validator ms`: `.autodev/task_quality_index.json` 의 task attempt + final validations duration 합/최대",
        "- `llm usage`: `.autodev/run_metadata.json` 의 `llm_usage` (tokens/chat/retries)",
        "- `retries`: `llm transport_retries + (total_task_attempts - tasks)`",
        "",
        "## 실패/타임아웃 내역",
        "",
    ]

    failed_rows = [r for r in [*before_rows, *after_rows] if not r.ok]
    if failed_rows:
        lines.extend([
            "| Lane | Repeat | ReturnCode | Error |",
            "|---|---:|---:|---|",
        ])
        for r in failed_rows:
            err = (r.error or "").strip().replace("\n", " ")
            if len(err) > 200:
                err = err[:200] + "..."
            lines.append(f"| {r.lane} | {r.repeat} | {r.returncode} | {err or '-'} |")
    else:
        lines.append("- 실패/타임아웃 없음")

    lines.extend([
        "",
        "## 한계 / 주의사항",
        "",
        "- LLM/네트워크 상태, 로컬 CPU 부하, 캐시 상태에 따라 변동폭이 큼",
        "- `peak RSS`는 `/usr/bin/time -l` (macOS) 또는 GNU time 출력 파싱에 의존",
        "- 실행 실패(run returncode != 0)도 CSV에 기록되며 평균에 포함됨",
        "- 동일 PRD라도 외부 모델 응답의 비결정성으로 결과가 완전히 재현되지 않을 수 있음",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="before/after 성능 비교 자동 리포트")
    p.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    p.add_argument("--prd", default=str(DEFAULT_PRD))
    p.add_argument("--profile", default="enterprise")
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))

    p.add_argument("--after-validators", default="ruff,pytest")
    p.add_argument("--after-max-fix-loops-total", type=int, default=2)
    p.add_argument("--after-max-fix-loops-per-task", type=int, default=1)
    p.add_argument("--after-max-json-repair", type=int, default=0)

    p.add_argument("--smoke", action="store_true", help="짧은 동작 검증 모드(기본 repeat=1, 경량 프로필/validator 적용)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    config_path = Path(args.config)
    prd_path = Path(args.prd)
    out_root = Path(args.out_root)
    repeat = args.repeat
    timeout = args.timeout

    if args.smoke:
        repeat = 1

    if repeat < 1:
        raise SystemExit("--repeat must be >= 1")

    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")
    if not prd_path.exists():
        raise SystemExit(f"PRD not found: {prd_path}")

    base_cfg = _load_yaml(config_path)
    selected_profile = args.profile
    profiles = base_cfg.get("profiles") or {}
    if args.smoke and selected_profile == "enterprise" and "enterprise_smoke" in profiles:
        selected_profile = "enterprise_smoke"
    if selected_profile not in profiles:
        raise SystemExit(f"Profile '{selected_profile}' not found in {config_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifact_dir = out_root / stamp
    artifact_dir.mkdir(parents=True, exist_ok=True)

    before_cfg = copy.deepcopy(base_cfg)
    after_cfg = copy.deepcopy(base_cfg)

    profile_cfg = copy.deepcopy(after_cfg.get("profiles", {}).get(selected_profile, {}))
    validators = profile_cfg.get("validators", [])
    requested = [v.strip() for v in args.after_validators.split(",") if v.strip()]
    if args.smoke and requested == ["ruff", "pytest"]:
        requested = ["ruff"]
    if requested and isinstance(validators, list):
        profile_cfg["validators"] = [v for v in requested if v in validators] or validators
    after_cfg.setdefault("profiles", {})
    after_cfg["profiles"][selected_profile] = profile_cfg

    run_cfg = copy.deepcopy(after_cfg.get("run", {}))
    run_cfg["max_fix_loops_total"] = args.after_max_fix_loops_total
    run_cfg["max_fix_loops_per_task"] = args.after_max_fix_loops_per_task
    run_cfg["max_json_repair"] = args.after_max_json_repair
    if args.smoke:
        run_cfg["max_fix_loops_total"] = min(_safe_int(run_cfg.get("max_fix_loops_total", 1)), 1)
        run_cfg["max_fix_loops_per_task"] = min(_safe_int(run_cfg.get("max_fix_loops_per_task", 1)), 1)
        run_cfg["max_json_repair"] = min(_safe_int(run_cfg.get("max_json_repair", 0)), 0)
    after_cfg["run"] = run_cfg

    before_cfg_path = artifact_dir / "config.before.yaml"
    after_cfg_path = artifact_dir / "config.after.yaml"
    _dump_yaml(before_cfg_path, before_cfg)
    _dump_yaml(after_cfg_path, after_cfg)

    rows: list[RunMetric] = []
    for lane, cfg_path in (("before", before_cfg_path), ("after", after_cfg_path)):
        for i in range(1, repeat + 1):
            metric = _run_once(
                lane=lane,
                repeat_idx=i,
                prd=prd_path,
                profile=selected_profile,
                out_root=artifact_dir / "runs",
                cfg_path=cfg_path,
                timeout_sec=timeout,
            )
            rows.append(metric)
            status = "PASS" if metric.ok else "FAIL"
            print(
                f"[{lane} #{i}] {status} wall={metric.wall_time_ms}ms "
                f"rss={metric.peak_rss_kb}KB validator={metric.validator_total_ms}ms "
                f"tokens={metric.llm_total_tokens} retries={metric.retries_total}"
            )

    csv_path = artifact_dir / "results.csv"
    md_path = artifact_dir / "report.md"
    json_path = artifact_dir / "results.json"

    _write_csv(csv_path, rows)
    json_path.write_text(json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False), encoding="utf-8")

    before_rows = [r for r in rows if r.lane == "before"]
    after_rows = [r for r in rows if r.lane == "after"]
    _write_markdown(md_path, before_rows, after_rows, args, selected_profile)

    print("\nArtifacts:")
    print(f"- {csv_path}")
    print(f"- {md_path}")
    print(f"- {json_path}")

    # 스크립트 동작 자체는 결과 파일 생성 기준으로 성공 처리.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
