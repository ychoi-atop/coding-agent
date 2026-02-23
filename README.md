# AutoDev Agent (Enterprise+) — PRD.md → Code/Test/CI/Docker/Security/Semgrep/SBOM 자동 생성

이 프로젝트는 **상세 Markdown PRD(PRD.md)** 를 입력으로 받아:

1) PRD → **엄격 JSON 구조화** (JSONSchema 검증 + 자동 수리)
2) **PLAN(태스크 그래프)** 생성 (작은 PR 단위)
3) 템플릿 기반 repo 스캐폴드 생성
4) 태스크 단위로 코드/테스트 작성 (LLM)
5) 로컬에서 **실제 실행 검증**: ruff / mypy / pytest / bandit / pip-audit / semgrep / SBOM / docker build
6) 실패 시 로그 기반 **자동 수정 루프(Self-healing)**
7) 최종 산출물:
   - `generated_runs/<prd파일명>_<timestamp>/` (동작하는 소프트웨어)
   - `.autodev/` (prd_struct.json, plan.json, task별 검증 로그, REPORT.md)

---

## 핵심 설계 포인트
- LLM은 **글/코드 생성**만 수행합니다.
- 검증(테스트/스캔/빌드)은 **로컬 실행 커널**이 담당합니다.
- 파일 수정은 기본적으로 **patch(unified diff)** 기반을 권장합니다. (리그레션 감소)
- 실행 커널은 **명령 allowlist**로 제한됩니다.

---

## 요구사항
- Python 3.10+
- (옵션) Docker (docker build 검증 시)
- LLM endpoint (OpenAI-compatible)
  - LM Studio local server 또는 OpenAI API 호환

---

## 설치
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 실행
예시 PRD:
```bash
python -m autodev.main --prd examples/PRD.md --out ./generated_runs --profile enterprise
```
위 명령은 예를 들어 `generated_runs/PRD_20260223_153045/` 같은 실행별 디렉터리를 생성합니다.

---

## PRD 템플릿(권장)
PRD가 자유 형식이어도 동작하지만, 아래 형식을 권장합니다.

- # Title
- ## Goals
- ## Non-Goals
- ## Personas (optional)
- ## Features
  - ### Feature A
  - ### Feature B
- ## Acceptance Criteria
- ## Non-Functional Requirements (NFR)
  - latency_ms: ...
  - security: ...
  - observability: ...
- ## Constraints (optional)

---

## Security / Compliance
- `ExecKernel`은 allowlist된 명령만 실행합니다.
- semgrep 규칙은 `.semgrep.yml` (로컬 규칙) 기반으로 동작합니다.
- SBOM은 `scripts/generate_sbom.py`로 CycloneDX JSON을 생성합니다.
- License report도 동일 스크립트에서 생성합니다.

---

## 주의
- `pip-audit`는 네트워크/환경 영향이 있습니다. `config.yaml`에서 `audit_required`로 fail/warn을 선택하세요.
