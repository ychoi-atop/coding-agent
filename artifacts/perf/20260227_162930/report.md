# 성능 비교 리포트 (before vs after)

- 생성 시각: 2026-02-27T16:45:31
- PRD: `/Users/ychoi/Documents/GitHub/coding-agent/docs/ops/benchmark_smoke_prd.md`
- profile: `enterprise_smoke`
- 반복 횟수: before=1, after=1
- smoke 모드: `True`

## 평균 지표

| Metric | Before(avg) | After(avg) | Delta(after-before) |
|---|---:|---:|---:|
| wall time (ms) | 480234.0 | 480258.0 | +24.0 |
| peak RSS (KB) | 47872.0 | 48384.0 | +512.0 |
| validator total (ms) | 0.0 | 0.0 | +0.0 |
| validator max (ms) | 0.0 | 0.0 | +0.0 |
| llm total tokens | 0.0 | 0.0 | +0.0 |
| retries total | 0.0 | 0.0 | +0.0 |

## 핵심 이벤트 정의

- `validator ms`: `.autodev/task_quality_index.json` 의 task attempt + final validations duration 합/최대
- `llm usage`: `.autodev/run_metadata.json` 의 `llm_usage` (tokens/chat/retries)
- `retries`: `llm transport_retries + (total_task_attempts - tasks)`

## 실패/타임아웃 내역

| Lane | Repeat | ReturnCode | Error |
|---|---:|---:|---|
| before | 1 | 1 | {   "ts": "2026-02-27T07:29:31Z",   "event": "autodev.run_cli_start",   "run_id": "68a2af7c74a74138bfcc4b103a368967",   "request_id": "c601460601364857adc013f9fe57a544",   "profile": "enterprise_smoke... |
| after | 1 | 1 | {   "ts": "2026-02-27T07:37:31Z",   "event": "autodev.run_cli_start",   "run_id": "4eb2bf81e459414c85cfbeb692084c96",   "request_id": "3c8f00451eb445a1a108ce602d335931",   "profile": "enterprise_smoke... |

## 한계 / 주의사항

- LLM/네트워크 상태, 로컬 CPU 부하, 캐시 상태에 따라 변동폭이 큼
- `peak RSS`는 `/usr/bin/time -l` (macOS) 또는 GNU time 출력 파싱에 의존
- 실행 실패(run returncode != 0)도 CSV에 기록되며 평균에 포함됨
- 동일 PRD라도 외부 모델 응답의 비결정성으로 결과가 완전히 재현되지 않을 수 있음
