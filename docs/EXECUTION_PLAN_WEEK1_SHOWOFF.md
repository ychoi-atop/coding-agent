# EXECUTION PLAN — Week 1 (Showoff Top5)

## 0) 목표 및 범위

이번 1주 계획의 목표는 **Showoff 데모 신뢰성(P0) 확보**입니다.  
우선순위 Top5 티켓을 7일 내 구현/검증/데모 리허설까지 마무리합니다.

- 대상 티켓: **SHW-001, SHW-008, SHW-002, SHW-003, SHW-004**
- 산출 목표:
  - 상태 정규화 계약 안정화
  - 데모용 fixture 데이터 생성 자동화
  - artifact JSON 에러 처리 고도화
  - start/resume API 엔드포인트 구현(dry-run + execute/resume)
  - 데모용 일일 검증 루틴 정착

---

## 1) Top5 티켓 매핑 (의존성 포함)

| 순번 | 티켓 | 핵심 결과 | 선행 의존성 | 주 담당 |
|---|---|---|---|---|
| 1 | SHW-001 | run status normalization contract 통합 + 테스트 | 없음 | Backend |
| 2 | SHW-008 | deterministic demo fixture 생성 스크립트 | 없음 | Platform |
| 3 | SHW-002 | malformed JSON typed error 정책 반영 | SHW-001 | Backend |
| 4 | SHW-003 | start run API(dry-run/execute) 구현 | SHW-001 | Backend |
| 5 | SHW-004 | resume run API 구현 + 4xx validation | SHW-003 | Backend |

---

## 2) Day 1 ~ Day 7 상세 일정

## Day 1 — Kickoff + 계약 고정
**목표**
- Week 1 범위를 잠그고 SHW-001 설계/구현 착수

**작업**
- SHW-001 상태 매핑 계약(`ok/failed/running/unknown`) 인터페이스 정의
- `gui_mvp_server`와 `gui_api`의 기존 상태 계산 경로 인벤토리
- 공통 mapper 도입 초안 + 단위테스트 skeleton 생성
- SHW-008용 fixture 스키마 요구사항 정리

**완료조건 (DoD)**
- 상태 계약 문서화 초안 완료
- 공통 mapper가 최소 한 경로에 연결되어 테스트 1차 통과
- SHW-008 입력/출력 스펙 문서화

**Top5 매핑**: SHW-001, SHW-008(준비)

---

## Day 2 — SHW-001 완료 + SHW-008 구현
**목표**
- SHW-001 완료, SHW-008 구현 시작/완료

**작업**
- SHW-001: metadata/checkpoint 충돌 케이스 포함 테스트 보강
- SHW-001: 양 서버 경로에 공통 mapper 완전 적용
- SHW-008: `ok/failed/running` 샘플 run 생성 스크립트 구현
- fixture가 `.autodev` 기대 스키마와 일치하는지 검증

**완료조건 (DoD)**
- SHW-001 acceptance criteria 100% 충족
- SHW-008 스크립트 실행 1회로 샘플 3종 생성 성공
- 생성 데이터 기준 `/api/runs` 기본 조회 정상

**Top5 매핑**: SHW-001, SHW-008

---

## Day 3 — SHW-002 구현/검증
**목표**
- artifact JSON 로딩 오류를 서버 예외가 아닌 typed error로 전환

**작업**
- SHW-002: malformed JSON 처리 정책 반영
- path + reason code 포함 에러 payload 포맷 확정
- 기존 테스트 업데이트(정상/비정상/경계 케이스)
- 의도적 손상 fixture로 회귀 테스트 수행

**완료조건 (DoD)**
- malformed JSON에서 500/traceback 미노출
- 에러 응답에 파일 경로와 reason code 포함
- 관련 테스트 모두 통과

**Top5 매핑**: SHW-002

---

## Day 4 — SHW-003 (Start API) 구현
**목표**
- start run API를 dry-run + execute 모드로 제공

**작업**
- SHW-003: endpoint 스펙(`prd/out/profile/model/interactive/config`) 구현
- dry-run: command preview + audit event 반환
- execute: shell disabled 상태로 subprocess spawn
- 입력 검증 및 실패 케이스(경로/옵션) 테스트 작성

**완료조건 (DoD)**
- dry-run 호출 시 명령 프리뷰와 audit 정보 확인 가능
- execute 호출 시 프로세스 spawn 성공/실패가 명시적으로 반환
- 보안 요구(shell disabled) 준수 검증 완료

**Top5 매핑**: SHW-003

---

## Day 5 — SHW-004 (Resume API) 구현
**목표**
- resume endpoint 안정화 + 4xx validation 보장

**작업**
- SHW-004: `--resume` 일관 부착 로직 구현
- spawn status + audit event 응답 포맷 통일
- invalid input path/token에 대한 4xx 처리 구현
- start/resume API 공통 유틸 리팩터링(중복 제거)

**완료조건 (DoD)**
- resume API가 항상 `--resume` 포함한 명령을 생성
- 잘못된 입력에서 4xx + 설명 가능한 에러 응답 반환
- SHW-003/004 통합 테스트 통과

**Top5 매핑**: SHW-004

---

## Day 6 — 통합 리허설 + 데모 시나리오 고정
**목표**
- Top5 통합 검증 및 데모 흐름 고정

**작업**
- SHW-001~004,008 E2E 스모크 실행
- fixture 생성 → `/healthz` → `/api/runs` → detail → start dry-run → start execute → resume 순서 검증
- 실패 시나리오 리허설(JSON 오류/잘못된 입력/빈 runs root)
- 데모 스크립트 및 운영자 체크 포인트 정리

**완료조건 (DoD)**
- 통합 플로우 2회 연속 성공
- 실패 시나리오별 fallback 절차 문서화 완료
- 데모 진행 시간(15분 기준) 리허설 완료

**Top5 매핑**: SHW-001,008,002,003,004 (통합)

---

## Day 7 — 버퍼/하드닝 + Go/No-Go
**목표**
- 잔여 이슈 정리 후 데모 실행 가능 상태 확정

**작업**
- 잔버그 수정 및 테스트 안정화
- 문서/명령 예시/운영 체크리스트 최종 정리
- Go/No-Go 리뷰(리스크, 우회 경로, 당일 운영 책임자)
- 최종 데모 드라이런 1회

**완료조건 (DoD)**
- 치명 이슈 0건(데모 차단 이슈 기준)
- 핵심 플로우 성공률 100%(드라이런 기준)
- Go 결정 및 당일 fallback 시나리오 합의 완료

**Top5 매핑**: 전체 마감

---

## 3) 리스크 및 Fallback

| 리스크 | 영향 | 조기 징후 | Fallback |
|---|---|---|---|
| 상태 파생 로직 숨은 케이스 누락(SHW-001) | run list/detail 불일치 | fixture 간 status mismatch | unknown으로 안전 fallback + 원인 로깅 + 케이스 추가 |
| fixture 스키마 드리프트(SHW-008) | 데모 데이터 재현 실패 | `/api/runs` 파싱 경고/누락 | fixture 버전 태그 고정 + 최소 샘플셋 백업 보관 |
| malformed JSON 처리 미흡(SHW-002) | 500 에러 노출 | traceback/uncaught exception | typed error 강제 반환, 문제 run skip 후 데모 지속 |
| start spawn 환경차(PATH/권한) (SHW-003) | execute 실패 | dry-run은 성공, execute만 실패 | dry-run 중심 데모 전환 + 사전 준비된 실행 로그 제시 |
| resume 입력 검증 누락(SHW-004) | 잘못된 재개 요청/오동작 | 비정상 path/token 통과 | strict 4xx gate + resume 대상 화이트리스트 확인 |

---

## 4) 매일 데모 검증 체크리스트 (Daily)

아래 체크리스트는 Day 1~Day 7 매일 종료 전에 수행합니다.

### A. 환경/서버
- [ ] Python/의존성 상태 정상 (`python --version`, requirements 충족)
- [ ] GUI/API 서버 기동 성공
- [ ] `/healthz` 응답 정상 (`ok=true`)

### B. 데이터/조회
- [ ] fixture 또는 실제 runs root 준비 확인
- [ ] `/api/runs` non-empty 및 상태 분포(ok/failed/running) 확인
- [ ] 임의 run detail 조회 1건 이상 성공

### C. Top5 기능 회귀
- [ ] SHW-001: 상태 정규화 결과가 서버 간 일관
- [ ] SHW-008: fixture 생성 스크립트 재실행 시 결정적 결과 유지
- [ ] SHW-002: malformed JSON에서 typed error 반환
- [ ] SHW-003: start dry-run + execute 응답 정상
- [ ] SHW-004: resume 호출 및 4xx 검증 정상

### D. 데모 운영성
- [ ] 15분 데모 핵심 동선 1회 리허설
- [ ] 실패 시나리오 1개 이상 fallback 리허설
- [ ] 당일 변경사항/리스크를 로그에 기록

---

## 5) 주간 완료 판정 기준 (Week 1 Exit Criteria)

- Top5 티켓 acceptance criteria 충족(코드 + 테스트 + 문서)
- 데모 핵심 플로우 연속 2회 성공
- 실패 시나리오별 fallback 즉시 실행 가능
- 운영자 관점에서 “데모 가능(Go)” 합의 완료
