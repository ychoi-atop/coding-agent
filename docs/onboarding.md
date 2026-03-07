# Onboarding Runbook

## 1) Who this is for
- New contributors to the AutoDev repo
- New project owners using generated run artifacts

## 2) One-time local setup

Recommended (idempotent demo bootstrap):

```bash
# from repo root
make demo-bootstrap
```

Equivalent direct command:

```bash
bash scripts/demo_bootstrap.sh
```

`demo-bootstrap` performs setup + fixture seed + local-simple launch/health checks and then exits.
To keep local-simple running for a live demo session:

```bash
make demo-bootstrap-serve
```

> Note: `scripts/demo_bootstrap.sh` enforces Python 3.11+ and expects `curl` in PATH.

Manual setup:

```bash
# from repo root
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Verify CLI:

```bash
autodev --help
```

Optional docs sanity check (before docs-only PRs):

```bash
make check-docs
```

## 3) Configure credentials

⚡ **요약:** LLM 백엔드는 `config.yaml`의 `llm.base_url / api_key / model`만 바꿔서 전환할 수 있습니다. 전환 직후에는 아래 체크리스트(30초)로 동작 검증.

AutoDev requires an OpenAI-compatible backend for the LLM client.

You can keep LM Studio defaults, or switch to another provider like Ollama.

### OpenClaw 동기화 권장값 (현재 운영 기준: oauth-bridge)

```yaml
llm:
  base_url: "http://127.0.0.1:18789/v1"
  api_key: ${AUTODEV_LLM_API_KEY}  # non-empty dummy allowed (ex: "openclaw-dummy")
  oauth_token: ""
  model: "anthropic/claude-opus-4-6"
  models:
    - base_url: "http://127.0.0.1:18789/v1"
      model: "anthropic/claude-opus-4-6"
      api_key: ${AUTODEV_LLM_API_KEY}
      oauth_token: ""
```

브릿지 경유 시 AutoDev는 Bearer 헤더를 만들기 위해 비어있지 않은 토큰 문자열이 필요합니다.
`AUTODEV_LLM_API_KEY="openclaw-dummy"` 같은 placeholder를 사용하세요(실제 민감정보 금지).

### LM Studio (default)

```bash
export AUTODEV_LLM_API_KEY="<lm-studio-key>"  # or keep as is
```

```yaml
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: ${AUTODEV_LLM_API_KEY}
  model: "qwen3-coder-30b-a3b-instruct-mlx"
```

### Ollama

```bash
# Ollama key is optional for local mode; pass a non-empty dummy token.
export AUTODEV_LLM_API_KEY="ollama"
```

```yaml
llm:
  base_url: "http://127.0.0.1:11434/v1"
  api_key: ${AUTODEV_LLM_API_KEY}
  model: "llama3.1:8b"
```

Make sure Ollama is running (`ollama serve`) and the model exists locally (`ollama list`).

You can also point to any OpenAI-compatible endpoint by changing only `base_url`, `api_key`, and `model`.

### 빠른 전환 체크리스트(30초)

- OpenRouter/일반 OpenAI-compatible: `AUTODEV_LLM_API_KEY` 설정
- OAuth 전용 게이트웨이 사용 시에만: `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN` 설정 + `api_key` 비우기
- `config.yaml`의 `llm` 블록에서 `base_url`, `api_key` 또는 `oauth_token`, `model` 수정
- 해당 백엔드의 모델/서비스 실행 상태 확인 (`ollama serve`, `ollama list`, 게이트웨이 헬스체크)
- `autodev --help` 또는 샘플 PRD 실행으로 한 번 동작 테스트

#### 성능 스모크(생성 사이클 벤치)

빠른 생성 루프 점검이 필요하면 기본 제공 샘플 PRD로 벤치마크도 바로 실행할 수 있습니다.

```bash
make benchmark-generate
```

- `baseline`: 기본 검사 강도로 1회
- `optimized`: 벤더/루프 제한을 줄여 가벼운 경로로 1회
- 실측값은 `Makefile`의 fast/strict 분리와 함께 비교해 루프 최적화를 진행하세요.

#### 최소 검증용 샘플 PRD 템플릿

아래처럼 아주 짧은 PRD 하나를 임시로 만들어, provider 스왑 직후 바로 `autodev` 동작을 확인하세요.

```bash
cat > /tmp/minimal-autodev-smoke-prd.md <<'PRD'
# Smoke test

## Goals
- Validate LLM backend connectivity through AutoDev.

## Non-Goals
- Produce production-grade features.

## Features
- Implement a function that adds two integers.

## Acceptance Criteria
- Generates a runnable Python project.
- At least one unit test exists for the addition function.
- `autodev` run exits with a success exit code.

PRD

autodev --prd /tmp/minimal-autodev-smoke-prd.md --out ./generated_runs --profile enterprise
```

OpenClaw bridge 전용으로는 아래 profile을 바로 사용할 수 있습니다.

```bash
AUTODEV_LLM_API_KEY="openclaw-dummy" \
python -m autodev.main --prd /tmp/minimal-autodev-smoke-prd.md --out ./generated_runs --profile openclaw-oauth-bridge
```

#### Claude (via OpenAI-compatible gateway)

AutoDev의 클라이언트는 OpenAI 형식이므로, Claude를 바로 쓰려면 OpenRouter, LiteLLM, 또는 자체 프록시처럼
`chat/completions`를 제공하는 게이트웨이를 앞단에 둡니다.

> ⚠️ OpenRouter는 `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN` 인증을 지원하지 않습니다.
> OpenRouter를 사용할 때는 반드시 `AUTODEV_LLM_API_KEY`를 사용하세요.

```yaml
llm:
  base_url: "https://openrouter.ai/api/v1"  # 또는 자체 게이트웨이 주소
  api_key: "<gateway-key>"
  model: "anthropic/claude-opus-4-6"
```

OAuth-compatible 게이트웨이(문서에서 OAuth Bearer + `chat/completions`를 명시한 경우만):

```yaml
llm:
  base_url: "https://<oauth-compatible-gateway>/v1"
  api_key: ""
  oauth_token: ${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}
  model: "<provider-model-id>"
```

#### 런타임 모델 오버라이드 (config 수정 없이)

Claude Opus 4.6을 기본값으로 두고, 실행 시점에만 모델을 바꾸고 싶다면 아래 우선순위를 사용하세요.

```bash
# 1) 환경변수 오버라이드
export AUTODEV_LLM_MODEL="anthropic/claude-opus-4-6"

# 2) CLI 오버라이드 (최우선)
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise --model "anthropic/claude-opus-4-6"
```

우선순위: `--model` > `AUTODEV_LLM_MODEL` > `config.yaml` (`llm.model`)

#### Codex 계열 모델(게이트웨이 경유)

Codex/Codex-style 모델도 직접 대응되지 않을 수 있어요. OpenAI-compatible API를 래핑해주는 프록시가 있는 경우:

```yaml
llm:
  base_url: "https://<your-gateway>/v1"
  api_key: "<gateway-key>"
  model: "<codex-compatible-model-name>"
```

와 같은 방식으로 `base_url/api_key/model`만 바꾸면 됩니다.

Profile behavior notes:
- Profile fields must include `template_candidates` and `validators`.
- `--profile` may be omitted only when exactly one profile is defined.
- Keep execution policy under `quality_profile` (`quality_profile.validator_policy`, `per_task_soft`, `final_soft`);
  top-level `validator_policy` is still accepted only as fallback.

Then confirm config:

```bash
python - <<'PY'
import yaml
print(yaml.safe_load(open('config.yaml').read())['llm'].keys())
PY
```

## 4) Minimal “hello” run

```bash
# run in repo root
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

- Output is created at `<out>/<prd-stem>_<YYYYMMDD_HHMMSS>/`
- On success you'll get a JSON-like message like `{ok: true, out: ...}`
- On failure, command exits non-zero and writes failure artifacts under the run directory.

### Optional: launch operator UI (recommended local-simple)

```bash
autodev local-simple --runs-root ./generated_runs --open
```

Alternative hardened mode (shared/non-localhost environments):

```bash
autodev gui --runs-root ./generated_runs --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`.

Current local-simple operator controls include quick-run start/resume/stop/retry, Process panel history, and Artifact Viewer links. Remaining MVP limits are polling-only updates and non-versioned artifact schema.

## 5) First actions for a generated run

```bash
cd ./generated_runs/<run-folder>
ls -la
cat .autodev/REPORT.md
cat .autodev/run_metadata.json
```

Common follow-up checks:

```bash
# basic repo hygiene
python -m ruff check src tests
python -m mypy src
python -m pytest -q
```

## 6) Runbook cheat sheet for generated FastAPI projects

```bash
cd ./generated_runs/<run-folder>
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl -sS http://127.0.0.1:8000/health
```

## 7) Template CI contract
- Source root: `autodev/`
- Run outputs: `generated_runs/*/.autodev/`
- Template validation contract: `docs/ops/template-validation-contract.json`
- CI validators (required): `ruff`, `mypy`, `pytest`, `pip_audit`, `bandit`, `semgrep`, `python scripts/generate_sbom.py` @ versions in contract
- Drift check command:
  ```bash
  bash docs/ops/check_template_ci_drift.sh
  ```

## 8) Showoff demo prep (optional)

```bash
# seed deterministic fixture runs
python3 scripts/showoff_seed_fixtures.py

# run GUI/API smoke test
bash scripts/showoff_demo_smoke.sh ./generated_runs
```

Planning and execution docs (active):
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/DEMO_PLAYBOOK.md`
- `docs/AUTONOMOUS_COMMERCIAL_PLAN.md` (commercial autonomous delivery strategy, refreshed in v1b)

Legacy showoff references:
- `docs/ROADMAP_SHOWOFF.md`
- `docs/BACKLOG_SHOWOFF.md`
