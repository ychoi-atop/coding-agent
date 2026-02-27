# 성능 비교 리포트 (before vs after)

- 생성 시각: 2026-02-27T14:00:01
- PRD: `/Users/ychoi/Documents/GitHub/coding-agent/docs/ops/benchmark_smoke_prd.md`
- profile: `enterprise`
- 반복 횟수: before=1, after=1
- smoke 모드: `True`

## 평균 지표

| Metric | Before(avg) | After(avg) | Delta(after-before) |
|---|---:|---:|---:|
| wall time (ms) | 148.0 | 130.0 | -18.0 |
| peak RSS (KB) | 45088.0 | 40128.0 | -4960.0 |
| validator total (ms) | 0.0 | 0.0 | +0.0 |
| validator max (ms) | 0.0 | 0.0 | +0.0 |
| llm total tokens | 0.0 | 0.0 | +0.0 |
| retries total | 0.0 | 0.0 | +0.0 |

## 핵심 이벤트 정의

- `validator ms`: `.autodev/task_quality_index.json` 의 task attempt + final validations duration 합/최대
- `llm usage`: `.autodev/run_metadata.json` 의 `llm_usage` (tokens/chat/retries)
- `retries`: `llm transport_retries + (total_task_attempts - tasks)`

## 한계 / 주의사항

- LLM/네트워크 상태, 로컬 CPU 부하, 캐시 상태에 따라 변동폭이 큼
- `peak RSS`는 `/usr/bin/time -l` (macOS) 또는 GNU time 출력 파싱에 의존
- 실행 실패(run returncode != 0)도 CSV에 기록되며 평균에 포함됨
- 동일 PRD라도 외부 모델 응답의 비결정성으로 결과가 완전히 재현되지 않을 수 있음
