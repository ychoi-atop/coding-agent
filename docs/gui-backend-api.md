# GUI Backend API (Step 1~3)

이 문서는 `docs/gui-requirements.md`의 P0 요구사항 중 GUI 백엔드 구현 상태를 정리한다.

- **Run lifecycle visibility**: run list/detail read API
- **Artifact access**: `.autodev/*` artifact read API
- **Launch and resume controls**: start/resume trigger wrapper (안전한 커맨드 구성)

## 구현 위치

- `autodev/gui_api.py`
- 테스트: `autodev/tests/test_gui_api.py`

## Read API 스캐폴딩

### 1) Run list

```python
list_runs(out_root: str, limit: int = 50) -> list[dict]
```

- `out_root` 하위 run 디렉터리를 스캔
- `.autodev/run_metadata.json`, `.autodev/checkpoint.json` 기반 상태 정규화
- 반환 필드(요약):
  - `run_id`, `request_id`, `run_name`, `run_dir`
  - `status`, `profile`, `model`, `started_at`, `completed_at`

### 2) Run detail

```python
get_run_detail(out_root: str, run_key: str) -> dict
```

- `run_key`는 run 폴더명 또는 `run_id` 지원
- 읽기 대상:
  - `.autodev/run_metadata.json`
  - `.autodev/checkpoint.json`
  - `.autodev/run_trace.json`
- JSON 파싱 실패가 있어도 함수는 crash하지 않으며, `artifact_errors` 배열로 구조화된 에러를 반환
- 응답에 `artifact_schema_versions`, `artifact_schema_warnings` 필드 포함
  - 버전 필드(`schema_version`, `artifact_schema_version`)가 없으면 `legacy-v0` fallback marker 사용
  - 알 수 없는 버전이면 warning object(`code=unknown_schema_version`)를 payload에 포함하고 fallback 경로 유지

### 3) Artifact read

```python
read_artifact(out_root: str, run_key: str, artifact_rel_path: str, max_bytes: int = 512000) -> dict
```

- `.autodev/` 하위만 허용 (path traversal 방지)
- `.json`은 파싱된 객체, `.md`는 markdown text 반환
- malformed/partial `.json`은 예외 대신 typed error payload로 반환
  - `error.kind = "artifact_json_error"`
  - `error.code = "artifact_json_malformed" | "artifact_json_truncated"`
  - `error.path`, `error.message`, `error.line`, `error.column`, `error.position`
- 대용량 응답은 `truncated=True`로 표시 (JSON 파싱 실패 시 `artifact_json_truncated` code)
- key artifact JSON (`run_metadata/checkpoint/run_trace/task_quality_index/task_final_last_validation`)는
  `artifact_schema` marker를 포함하며, unknown version이면 `warning` object를 함께 반환

## Run status normalization contract (SHW-001)

구현 모듈: `autodev/run_status.py`

정규화된 상태는 아래 4개만 허용한다.

- `ok`
- `failed`
- `running`
- `unknown`

판정 우선순위:

1. `run_metadata.result_ok` (`True -> ok`, `False -> failed`)
2. `task_quality_index.final.status` (alias 정규화)
3. `checkpoint.status` (alias 정규화)
4. fallback (`default`)

대표 alias 매핑:

- `completed`, `success`, `passed` → `ok`
- `error`, `blocked`, `timeout`, `cancelled` → `failed`
- `in_progress`, `queued`, `partial` → `running`

`gui_api`와 `gui_mvp_server` 모두 동일 모듈을 사용해 상태를 계산한다.

## Compatibility adapter layer (SHW-015)

구현 모듈: `autodev/gui_mvp_dto.py`

`/api/runs/<run_id>` 상세 응답에서 run artifact를 버전 차이와 무관하게 안정적인 DTO로 정규화한다.

- run trace 정규화 (`normalize_run_trace`)
  - 지원 변형 1: legacy event stream (`events[]` + `phase.start/phase.end`)
  - 지원 변형 2: modern timeline (`phases[]` 또는 `phase_timeline[]`)
  - 안정 필드: `model`, `profile`, `run_id`, `request_id`, `started_at`, `completed_at`, `phase_timeline[]`
- validation 정규화 (`normalize_validation`)
  - 지원 변형 1: `validation|validations|results|rows`
  - 지원 변형 2: legacy nested (`final.validations[]`) + quality fallback (`quality_index.final.validations[]`)
  - per-task `tasks[].last_validation[]`를 triage용 row로 병합
  - 안정 필드: `summary(total/passed/failed/soft_fail/skipped/blocking_failed)`, `validator_cards[]`

테스트:
- unit + fixture snapshot: `autodev/tests/test_gui_mvp_dto.py`
- fixture 파일: `autodev/tests/fixtures/gui_compat/*.json`

## Run control 스캐폴딩

### 1) Start command wrapper

```python
build_start_command(payload: Mapping[str, Any]) -> list[str]
trigger_start(payload: Mapping[str, Any], execute: bool = False) -> dict
```

- 필수 입력: `prd`, `out`, `profile`
- 선택 입력: `model`, `interactive`, `config`
- 안전장치:
  - `shell=False` 기반 argv 구성
  - `profile/model` 토큰 allowlist 검증
  - path 인자 제어문자(`\0`, `\n`, `\r`) 차단

### 2) Resume command wrapper

```python
build_resume_command(payload: Mapping[str, Any]) -> list[str]
validate_resume_target(out_dir: str) -> dict
trigger_resume(payload: Mapping[str, Any], execute: bool = False) -> dict
```

- start 커맨드에 `--resume` 보강
- `validate_resume_target`로 resumable marker/checkpoint/metadata 일관성 검증
- dry-run(`execute=False`)에서도 audit payload 생성

### 3) Controlled process manager + stop/retry wrappers (SHW-017)

구현 모듈: `autodev/gui_process_manager.py`

- 실행 모드(`execute=true`)의 start/resume 요청은 in-memory tracked process로 등록된다.
- 각 process는 `process_id`를 가지며 `transitions[]`로 상태 전이를 남긴다.
  - 예: `spawned -> running -> stopping -> terminated|killed` 또는 `spawned -> running -> exited`
- stop/retry wrapper:

```python
trigger_stop(payload: Mapping[str, Any], graceful_timeout_sec: float = 2.0) -> dict
trigger_retry(payload: Mapping[str, Any], execute: bool = False) -> dict  # process_id 기반
```

- stop: `terminate()` graceful wait 후 timeout이면 `kill()` fallback
- retry: 기존 tracked process의 `command/payload/run_link`를 재사용하고
  `retry_of/retry_root/retry_attempt` 체인을 유지

## Audit 이벤트 영속화 (SHW-005)

mutating endpoint(`POST /api/runs/start`, `POST /api/runs/resume`, `POST /api/runs/stop`, `POST /api/runs/retry`)는 모든 요청 결과를 file-backed audit log로 남긴다.

- 구현 모듈: `autodev/gui_audit.py`
- 기본 저장 경로: `artifacts/gui-audit/gui-audit-YYYY-MM-DD.jsonl`
- 경로 override: `AUTODEV_GUI_AUDIT_DIR` (상대경로는 repo root 기준)

로그 필드(요약):

```json
{
  "timestamp": "<UTC ISO8601>",
  "action": "start|resume|stop|retry",
  "role": "evaluator|operator|developer",
  "auth": {
    "source": "token|session|header_or_env",
    "subject": "svc-operator",
    "scope": {"project": "payments", "environment": "prod"},
    "policy_name": "payments-prod",
    "policy_allowed_roles": ["operator"]
  },
  "payload": {
    "prd": "...",
    "out": "...",
    "profile": "...",
    "model": "...",
    "interactive": false,
    "process_id": "proc-...",
    "graceful_timeout_sec": 2.0,
    "project": "payments",
    "environment": "prod",
    "execute": false
  },
  "result_status": "forbidden|invalid_request|dry_run|spawned|stopped|not_found|launch_failed",
  "error": "...optional..."
}
```

- audit write 실패 시 API는 crash하지 않고 `500 audit_persist_failed`를 반환한다.

## HTTP Run Control Endpoints (SHW-003 / SHW-004 / SHW-017)

`autodev/gui_mvp_server.py`에 아래 mutating endpoint를 연결했다.

### `POST /api/runs/start`

- body(JSON object)
  - required: `prd`, `out`, `profile`
  - optional: `model`, `interactive`, `config`, `execute`
- `execute=false` (default): dry-run 응답 (`spawned=false`) + audit_event
- `execute=true`: `shell=False`로 subprocess spawn 시도 (`spawned=true`, `pid` 포함)

### `POST /api/runs/resume`

- start payload와 동일
- 내부 명령은 항상 `--resume` 포함
- 추가 검증: `out`은 **기존 run 디렉터리**를 직접 가리켜야 하며, 아래 resumable marker가 일관되어야 함
  - `.autodev/checkpoint.json`
  - `.autodev/run_metadata.json`
  - checkpoint의 resumable marker(`completed_task_ids` 또는 `failed_task_id`)
  - `run_id` consistency(checkpoint/run_metadata)
- terminal 상태(`ok/failed`) run은 resume 대상에서 거부

### `POST /api/runs/stop` (SHW-017)

- body(JSON object)
  - required: `process_id`
  - optional: `graceful_timeout_sec` (default `2.0`)
- 동작
  - tracked process에 `terminate()` 시도
  - `graceful_timeout_sec` 내 종료되지 않으면 `kill()` fallback
- 응답
  - `process.state`: `terminated | killed | exited`
  - `process.transitions[]`에 stop 관련 상태 전이 기록

### `POST /api/runs/retry` (SHW-017)

- body(JSON object)
  - required: `process_id`
  - optional: `execute` (default `false`)
- 동작
  - 지정한 tracked process의 command/payload를 기반으로 재실행
  - retry chain metadata 유지
    - `retry_of`
    - `retry_root`
    - `retry_attempt`
  - run linkage(`run_link.out`) 유지

### `GET /api/gui/context`

GUI bootstrapping context를 제공한다 (mode/defaults/capabilities).

응답 요약:
- `mode`: `local_simple | hardened`
- `local_simple_mode`: boolean
- `defaults`
  - `profile`
  - `out`
  - `config`
  - `prd`
- `api.run_controls`: `["start", "resume", "stop", "retry"]`

### `GET /api/runs/compare?left=<run_id>&right=<run_id>` (SHW-012)

두 개의 run을 비교 가능한 공통 요약 스키마로 반환한다.

- query params
  - required: `left`, `right`
  - legacy alias 지원: `run_a`, `run_b`
- 반환 payload
  - `left`, `right`: normalized run summary
  - `delta`: `right - left` 기준 핵심 수치 차이

normalized summary 필드(핵심):
- `run_id`, `status`, `project_type`, `profile`, `model`
- `started_at`, `completed_at`, `updated_at`
- `totals`: `total_task_attempts`, `hard_failures`, `soft_failures`, `task_count`, `blocker_count`
- `validation`: `total`, `passed`, `failed`, `soft_fail`, `skipped`, `blocking_failed`
- `timeline`: `phase_count`, `total_duration_ms`
- `blockers`

누락 필드는 모두 explicit default로 정규화된다(문자열=`""`, 숫자=`0`, 배열=`[]`).

오류 semantics:
- `400 invalid_compare_query`: `left/right` 누락
- `404 run not found`: 지정한 run 디렉터리 없음

예시:

```bash
curl "http://127.0.0.1:8787/api/runs/compare?left=showoff_failed_001&right=showoff_ok_001"
```

### `GET /api/runs/trends?window=<N>&partial=<bool>` (SHW-018)

cross-run quality trend 요약(validator/blocker)을 제공한다.

- query params
  - optional: `window` (default `20`, min `1`, max `200`)
  - optional: `partial` 또는 `allow_partial` (`true/false`, default `false`)
- 동작
  - 최신 run부터 window 범위만 스캔
  - artifacts
    - `.autodev/task_quality_index.json`
    - `.autodev/task_final_last_validation.json`
  - 기본(strict, `partial=false`)은 기존 동작과 동일: 둘 중 하나라도 missing/malformed이면 skip
  - partial mode(`partial=true`)는 **정확히 하나의 artifact만 missing**인 run을 부분 집계로 포함
    - malformed(JSON invalid)는 partial mode에서도 skip(신뢰성 우선)
  - skip/include 원인은 `counters`에 명시적으로 누적

응답 payload(요약):
- `window`: `requested`, `applied`
- `mode.allow_partial`
- `counters`
  - `runs_total`, `runs_windowed`, `runs_included`
  - `runs_included_full`, `runs_included_partial`
  - `runs_included_partial_missing_quality`, `runs_included_partial_missing_validation`
  - `runs_skipped_missing_quality`, `runs_skipped_invalid_quality`
  - `runs_skipped_missing_validation`, `runs_skipped_invalid_validation`
  - `runs_skipped_missing_or_invalid_artifacts`
- `runs[]`
  - run별 `inclusion_mode` (`full|partial`), `artifact_availability`, `validator`, `blockers`
- `aggregates.validators`
  - `totals`, `by_name`
- `aggregates.blockers`
  - `total`, `unique`, `by_name`

예시:

```bash
# strict(default) - backward compatible
curl "http://127.0.0.1:8787/api/runs/trends?window=30"

# partial aggregation enabled
curl "http://127.0.0.1:8787/api/runs/trends?window=30&partial=true"
```

### RBAC + Auth integration (SHW-006 / SHW-016)

mutating endpoint는 최소 role matrix를 강제한다.

- `evaluator`: read-only (`POST /api/runs/start|resume|stop|retry` 모두 403)
- `operator`: start/resume/stop/retry 허용
- `developer`: start/resume/stop/retry 허용

role source 우선순위 (mutating endpoints):
1. bearer token (`Authorization: Bearer <token>`) 또는 `X-Autodev-Token`
2. session (`X-Autodev-Session` 또는 `Cookie: autodev_session=<id>`)
3. HTTP header `X-Autodev-Role`
4. env `AUTODEV_GUI_ROLE`
5. 기본값 `evaluator` (safest default)

Auth provider config (JSON file): `AUTODEV_GUI_AUTH_CONFIG=/path/to/auth.json`

```json
{
  "tokens": {
    "tok-operator": {"role": "operator", "subject": "svc-operator"}
  },
  "sessions": {
    "sess-001": {"role": "developer", "subject": "alice"}
  },
  "policies": [
    {
      "name": "payments-prod",
      "project": "payments",
      "environment": "prod",
      "actions": {
        "start": ["operator"],
        "resume": ["operator", "developer"],
        "stop": ["operator", "developer"],
        "retry": ["operator"]
      }
    }
  ],
  "default_policy": {
    "allowed_roles": ["operator", "developer"]
  }
}
```

scope는 request payload의 `project`, `environment` 필드(또는 env fallback `AUTODEV_GUI_PROJECT`, `AUTODEV_GUI_ENVIRONMENT`)로 매칭된다.

권한 부족 시:
- `403 Forbidden`
- payload: `error.code = "forbidden_role"`, `allowed_roles`, `auth_source`, `policy_allowed_roles` 포함
- `error.fix_hints[]`: GUI가 바로 보여줄 수 있는 deterministic 해결 가이드(정적 규칙 기반)
- audit 로그는 기존 필드(`role`, `payload`, `result_status`) 유지 + `auth` 메타정보 추가

### 4xx validation semantics

- `400 Bad Request`
  - 비어있는 body
  - JSON 파싱 실패
  - JSON object가 아닌 body
- `403 Forbidden`
  - RBAC에 의해 role이 mutating action을 수행할 수 없는 경우
- `422 Unprocessable Entity`
  - 필수 필드 누락/타입 오류 (`error.code` 포함)
  - `execute`가 boolean이 아닌 경우
  - `prd/config` 경로 유효성 실패
  - `profile/model` unsafe token
  - resume target marker/metadata/checkpoint consistency 실패
  - terminal run에 대한 resume 요청
  - stop의 `process_id` 누락
  - retry의 `process_id` 누락
  - stop의 `graceful_timeout_sec` 타입/범위 오류
- `404 Not Found`
  - 런타임에서 파일 경로가 발견되지 않는 경우

### Inline failure fix hints (SHW-019 local-simple UX)

mutating action 실패 응답의 `error` 객체에는 `fix_hints` 배열이 포함될 수 있다.

- 규칙 기반(rule-based)이며 모델 호출 없이 결정적(deterministic)
- 민감 정보(stdout/stderr, 토큰, 절대 경로 dump) 미포함
- 대표 매핑:
  - `missing_prd` / `invalid_prd` → PRD 경로 점검
  - `invalid_out` / `resume_out_missing` → Out 디렉터리/런 디렉터리 점검
  - `forbidden_role` → 허용 role/policy 점검
  - `invalid_payload`(resume finalized 등) → Resume 대신 Retry 권장
  - resume/retry 맥락에서 validation artifact가 있으면 failed validator 이름 기반 힌트 추가

## 현재 단계의 의도적 제한

- cancel, live streaming(SSE/WebSocket)은 미구현
- compatibility adapter는 run_trace + validation 범위(SHW-015)까지 구현됨. 기타 artifact별 세부 adapter는 후속 확장 대상
