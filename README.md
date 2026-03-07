# AutoDev Agent (Enterprise+)

AutoDev Agent turns a Markdown PRD into a working Python project with tests and validation artifacts.
It is designed for teams that want a repeatable **plan -> implement -> validate -> fix** loop they can run locally, inspect, and iterate on.

## Why this project
Shipping from a PRD is usually fragmented: planning in one place, code generation in another, and validation spread across scripts/CI.
AutoDev brings those steps into one orchestrated flow with explicit artifacts and bounded retries, so runs are easier to understand and debug.

## Who it's for
- Developers and tech leads who want a local-first PRD-to-code workflow
- Demo/review owners who need quick, reproducible run evidence
- Teams that prefer validation by local tools over model-only claims

## What you get
- **PRD-to-code execution:** PRD parsing, task planning, code generation, validator runs, and bounded auto-fix loops
- **Local-simple operator lane:** one-command local GUI start with **Quick Run** support
- **Operational visibility:** Process panel for active/recent run tracking and retry/stop actions
- **Debuggable artifacts:** read-only Artifact Viewer for `.autodev/*` outputs and validator triage
- **Run quality snapshot:** latest scorecard surfaced in the Overview (plus local demo scorecard generator)
- **Confidence checks:** deterministic local-simple E2E smoke lane and docs/runbooks for onboarding and handoff

## What This Agent Produces
- A runnable Python project scaffolded from templates (`python_fastapi` or `python_cli`).
- Implemented features and tests based on your PRD.
- Validation artifacts under `.autodev/` (structured PRD, plan, per-task checks, final report).
- Security and supply-chain artifacts such as SBOM and license reports.

## How The Agent Works
1. Parse PRD markdown into strict JSON (`PRD_SCHEMA`) with JSON repair retries.
2. Build an implementation plan (`PLAN_SCHEMA`) with small dependency-aware tasks.
3. Select template type (`python_fastapi` for API-style PRDs, otherwise `python_cli`).
4. Apply LLM-generated file changes (`CHANGESET_SCHEMA`) task-by-task.
5. Run local validators for each task focus.
6. If checks fail, ask the fixer role for targeted patches and re-run validators.
7. Run full final validation and write `.autodev/REPORT.md`.

## Why This Is Reliable
- Structured outputs are schema-validated before use.
- Validation is done by local tools, not by model self-assertion.
- Command execution is restricted by a strict allowlist in `ExecKernel`.
- Fix loops are bounded by configurable max iterations.
- Task submissions must include a mandatory handoff block (`Summary`, `Changed Files`, `Commands`, `Evidence`, `Risks`, `Next Input`). Missing fields trigger friendly re-request and are logged.

## Prompt Contract (Core + Optional)
Implementation/fix prompts are now split into:
- `core` (minimum slots): `goal`, `paths`, `constraints`, `output_format`
- `optional_context`: plan/task/files/validation details used only when needed

This keeps prompt payload lightweight while preserving required execution context.

## Requirements
- Python 3.10+
- Optional Docker (only needed for `docker_build` validation)
- OpenAI-compatible chat endpoint (LM Studio, Ollama, or any OpenAI-compatible API)

## Install

Recommended (demo bootstrap, idempotent):
```bash
make demo-bootstrap
```

Equivalent direct command:
```bash
bash scripts/demo_bootstrap.sh
```

This one-command lane will:
1) create/reuse `.venv` and install dependencies,
2) seed deterministic demo fixtures into `./generated_runs`,
3) launch `autodev local-simple`,
4) verify `/healthz`, `/api/runs`, and `/api/gui/context`, then stop server (check-only mode).

To keep the server running after checks:
```bash
make demo-bootstrap-serve
# or: bash scripts/demo_bootstrap.sh --serve --open
```

> Prerequisites for demo bootstrap: Python 3.11+, `make`, and `curl` available in PATH.

Manual setup:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional CLI installation:
```bash
pip install -e .
```

## Configure
Edit `config.yaml` for your model endpoint and run profile.

- `AUTODEV_LLM_API_KEY`: for OpenAI-compatible providers like OpenRouter.
- `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN`: only for gateways that explicitly support OAuth Bearer tokens on `chat/completions`.
  (OpenRouter does **not** support this token type.)

```bash
cp .env.example .env
# then fill your key/token in .env (never commit secrets)
```

```yaml
llm:
  base_url: "https://openrouter.ai/api/v1"
  api_key: ${AUTODEV_LLM_API_KEY}
  oauth_token: ""
  model: "anthropic/claude-opus-4-6"
  # optional fallback chain (retryable transport error 시 다음 endpoint 시도)
  models:
    - base_url: "https://openrouter.ai/api/v1"
      model: "anthropic/claude-opus-4-6"
      api_key: ${AUTODEV_LLM_API_KEY}
      oauth_token: ""
    - base_url: "https://openrouter.ai/api/v1"
      model: "openai-codex/gpt-5.3-codex"
      api_key: ${AUTODEV_LLM_API_KEY}
      oauth_token: ""
  timeout_sec: 240
```

### OpenClaw bridge profile (`openclaw-oauth-bridge`)

If you are routing through the local OpenClaw OAuth bridge, use:

```yaml
llm:
  base_url: "http://127.0.0.1:18789/v1"
  api_key: ${AUTODEV_LLM_API_KEY}  # non-empty dummy is OK (e.g. "openclaw-dummy")
  oauth_token: ""
  model: "anthropic/claude-opus-4-6"
```

Then run with the dedicated profile:

```bash
AUTODEV_LLM_API_KEY="openclaw-dummy" \
python -m autodev.main --prd /tmp/minimal-autodev-smoke-prd.md --out ./generated_runs --profile openclaw-oauth-bridge
```

## Switching LLM providers

⚡ **Quick summary:** You can switch LLM providers by updating only `llm.base_url`, `llm.api_key`, and `llm.model`. Verify in 30 seconds with the provider swap checklist below.

AutoDev is OpenAI-compatible transport only. To switch providers, only the `llm` block needs to change.

### OpenClaw model sync (recommended baseline)
- Primary: `anthropic/claude-opus-4-6`
- Fallback endpoint model: `openai-codex/gpt-5.3-codex`
- Keep auth in env vars only (`AUTODEV_LLM_API_KEY` or `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN`)
- Use the same OpenAI-compatible gateway URL in `llm.base_url` and `llm.models[*].base_url`

- LM Studio (local):

  ```yaml
  llm:
    base_url: "https://openrouter.ai/api/v1"
    api_key: ${AUTODEV_LLM_API_KEY}
    model: "anthropic/claude-opus-4-6"
  ```

- Ollama:

  ```yaml
  llm:
    base_url: "http://127.0.0.1:11434/v1"
    api_key: "ollama"  # Ollama does not require a real key
    model: "llama3.1:8b"
  ```

  - For Ollama, set `AUTODEV_LLM_API_KEY="ollama"` (or any non-empty string)
  - Confirm the model name exists locally: `ollama list`
  - Restart the server with `ollama serve`
  - If you prefer hard-coded YAML, place the dummy key directly in `config.yaml`

- Other OpenAI-compatible providers (ex: OpenRouter, Azure OpenAI, local gateways):
  - Update `base_url` to provider endpoint
  - Use a provider-compatible auth token in `api_key`
  - Use a model name the provider exposes

- OpenRouter example:

  ```yaml
  llm:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "<openrouter-api-key>"
    model: "qwen/qwen-2.5-coder-32b-instruct"
  ```

- OAuth-compatible gateway example (only if your gateway docs explicitly say OAuth Bearer on `chat/completions`):

  ```yaml
  llm:
    base_url: "https://<oauth-compatible-gateway>/v1"
    api_key: ""
    oauth_token: ${AUTODEV_CLAUDE_CODE_OAUTH_TOKEN}
    model: "<provider-model-id>"
  ```

- Azure OpenAI (through an OpenAI-compatible gateway or endpoint):

  ```yaml
  llm:
    base_url: "https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions"
    api_key: "<azure-api-key>"
    model: "<azure-deployment-name>"
  ```

  Azure 배포가 프록시를 거치지 않는 경우 `base_url`이 환경마다 다릅니다. 실제로는 아래와 같은 형태로
  게이트웨이/프로바이더 문서를 확인해 주세요.

- LiteLLM / Local proxy example:

  ```yaml
  llm:
    base_url: "http://localhost:4000/v1"
    api_key: "<proxy-key>"
    model: "gpt-4o-mini"
  ```

- Claude (via OpenAI-compatible gateway)

  Example with an OpenAI-compatible proxy/gateway that exposes Claude:

  ```yaml
  llm:
    base_url: "https://openrouter.ai/api/v1"  # or your proxy URL
    api_key: "<proxy-or-service-key>"
    model: "anthropic/claude-opus-4-6"
  ```

- Codex-style models (via compatible gateway)

  If your provider exposes a `chat/completions`-compatible model name, set it here:

  ```yaml
  llm:
    base_url: "https://<your-gateway>/v1"
    api_key: "<gateway-key>"
    model: "<codex-compatible-model-name>"
  ```

  If using GitHub Copilot/Codex tooling directly, run it through a translator/proxy that provides OpenAI API-compatible endpoints first.

```yaml
run:
  max_json_repair: 2
  max_fix_loops_total: 10
  max_fix_loops_per_task: 4
  verbose: true
  budget:
    max_tokens: 500000
```

### Authentication priority and safety
- Priority: `llm.api_key` (or `AUTODEV_LLM_API_KEY`) is used first for backward compatibility.
- Fallback: if API key is empty, `llm.oauth_token` (or `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN`) is used.
- Important: `llm.oauth_token` only works with gateways that support OAuth Bearer for `chat/completions`. OpenRouter requires API key auth.
- Never commit real keys/tokens to Git. Keep them in environment variables.
- Avoid printing token values in logs, shell history, and CI output.

### Quick provider swap checklist
- Set `AUTODEV_LLM_API_KEY` or `AUTODEV_CLAUDE_CODE_OAUTH_TOKEN`
- Update `llm.base_url`, `llm.model`, and (optionally) auth placeholders in config
- Ensure backend is running (`ollama serve` / gateway health endpoint)
- Run a dry-check (e.g. `autodev --help` or a small sample PRD run)

### Runtime model override (without editing config)
- Environment variable override:
  - `export AUTODEV_LLM_MODEL="anthropic/claude-opus-4-6"`
- CLI override (highest precedence):
  - `autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise --model "anthropic/claude-opus-4-6"`

Precedence: `--model` > `AUTODEV_LLM_MODEL` > `config.yaml` (`llm.model`).

### Performance knobs (generate cycles)
- `make fast` for quick iteration
- `make strict` (or `make ci`) before pushing/release
- `make benchmark-generate` for baseline vs optimized generation timing smoke
- `make perf-smoke` for lightweight performance snapshot from a generated run
- `make perf-strict` for conservative regression gate against previous `generated_dir/.autodev/perf.json`
- `python docs/ops/perf_compare_report.py --repeat 2` for before/after 자동 비교 리포트(`artifacts/perf/<timestamp>/`)

#### Before/After 성능 리포트 스크립트
동일 PRD 시나리오를 before/after 각각 반복 실행해 다음 지표를 수집합니다.
- wall time(ms)
- peak RSS(KB)
- validator ms(총합/최대)
- llm usage(tokens/chat calls/transport retries)
- retries(transport retries + task 재시도 유사 카운트)

실행 예시:
```bash
# 권장: 기본 2회 반복으로 비교
python docs/ops/perf_compare_report.py \
  --config config.yaml \
  --prd docs/ops/benchmark_smoke_prd.md \
  --profile enterprise \
  --repeat 2

# 스모크(짧은 검증): 1회 실행
python docs/ops/perf_compare_report.py --smoke
```

산출물:
- `artifacts/perf/<timestamp>/results.csv`
- `artifacts/perf/<timestamp>/report.md`
- `artifacts/perf/<timestamp>/results.json`

한계/주의:
- 네트워크/LLM provider/머신 부하에 따라 결과 편차가 큼
- peak RSS는 `/usr/bin/time -l` 또는 GNU time 출력 파싱에 의존
- 실행 실패 케이스도 CSV에 기록됨(원인 추적 용도)

### Spec-first + Test-first + Docs-as-code workflow
- Spec-first: PR에서 변경 목적/수용기준/비범위를 먼저 명시
- Test-first: 실패 재현 테스트를 먼저 추가 후 구현
- Docs-as-code: 동작/인터페이스 변경 시 README/docs를 코드와 함께 수정

Day-to-day command flow:
- 빠른 반복: `make ci-fast`
- 문서 검증: `make check-docs`
- 머지 전 게이트: `make ci-strict`

참고 체크리스트: `docs/ops/quality-gate-checklist.md`

```yaml
profiles:
  enterprise:
    template_candidates: ["python_fastapi", "python_cli"]
    validators: ["ruff", "mypy", "pytest", "pip_audit", "bandit", "semgrep", "sbom", "docker_build"]
    security:
      audit_required: false
    quality_profile:
      name: balanced
      validator_policy:
        per_task:
          soft_fail: ["docker_build", "pip_audit", "sbom", "semgrep"]
        final:
          soft_fail: ["pip_audit", "sbom", "semgrep"]
      per_task_soft: ["docker_build", "pip_audit", "sbom", "semgrep"]
      final_soft: ["pip_audit", "sbom", "semgrep"]
      # Disabled by default until validator-graph stabilization is complete.
      validator_graph:
        enabled: false
        mode: strict
        skip_on_soft_fail: false
        custom_edges: {}
      by_level:
        strict:
          per_task_soft: ["docker_build"]
          final_soft: []
```

## Profiles
Profile-specific execution settings live under `profiles` in `config.yaml`.

Built-in examples in this repo:
- `local_simple`: laptop/single-user fast loop (ruff + pytest, minimal policy friction)
- `enterprise`: broader validation set for hardened/release-oriented runs
- `enterprise_smoke`: smallest smoke lane
- `openclaw-oauth-bridge`: bridge connectivity smoke/dev lane

Required profile fields:
- `template_candidates` (non-empty list)
- `validators` (non-empty list)

Optional profile fields:
- `security.audit_required` (default `false`)
- `quality_profile` (execution policy, defaults to permissive runtime behavior)

CLI behavior:
- `--profile` defaults to the only defined profile when exactly one profile exists.
- If multiple profiles are defined, `--profile` is required to avoid ambiguous behavior.
- If the requested profile does not exist, CLI exits with a clear list of available names.

## Run
Use either module form:
```bash
python -m autodev.main --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

Or installed script form:
```bash
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

Resume a partial run from checkpoint:
```bash
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise --resume
```

Require manual confirmation before implementation:
```bash
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise --interactive
```

### Fully autonomous mode (unattended v1)
Run end-to-end unattended loop (ingest → plan → execute → self-verify → bounded auto-fix retries → report):
```bash
autodev autonomous start --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

Common policy overrides:
```bash
autodev autonomous start \
  --prd examples/PRD.md \
  --out ./generated_runs \
  --profile enterprise \
  --max-iterations 3 \
  --time-budget-sec 3600 \
  --workspace-allowlist "$(pwd)" \
  --blocked-paths "$(pwd)/secrets"
```

Resume an autonomous session from saved state:
```bash
autodev autonomous start --resume-state --run-dir ./generated_runs/<run_dir> --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

Inspect autonomous state:
```bash
autodev autonomous status --run-dir ./generated_runs/<run_dir>
```

Autonomous artifacts are written under the run directory:
- `.autodev/autonomous_state.json`
- `.autodev/autonomous_report.json`
- `AUTONOMOUS_REPORT.md`
- `.autodev/run_metadata.json` (includes optional `autonomous_quality_gate_policy` when configured)

See `docs/AUTONOMOUS_MODE.md` for detailed policy and operational behavior.
For commercial-grade autonomous delivery strategy and rollout governance, see `docs/AUTONOMOUS_COMMERCIAL_PLAN.md`.

Update (v1b, 2026-03-07): autonomous commercial rollout references were refreshed across README/onboarding/autonomous-mode docs for easier operator discovery.

### GUI launcher (MVP)
Hardened/default mode:
```bash
autodev gui --runs-root ./generated_runs --host 127.0.0.1 --port 8787
```

Local simple mode (recommended for single-user laptop workflow):
```bash
autodev local-simple --runs-root ./generated_runs
# optional: auto-open browser
autodev local-simple --runs-root ./generated_runs --open
# optional: auto-open + kickoff quick run on startup (best-effort)
autodev local-simple --runs-root ./generated_runs --open --run examples/PRD.md
```

Options:
- `--runs-root`: scan target for `<run_dir>/.autodev/*` artifacts (default: `generated_runs`)
- `--host`: bind address (default: `127.0.0.1`)
- `--port`: bind port (default: `8787`)
- `--open` (local-simple): best-effort open GUI URL in default browser on startup
- `--run <PRD_PATH>` (local-simple): best-effort quick-run kickoff on startup (non-fatal if kickoff fails)

Local simple mode quick notes:
- localhost-first bind safety (`127.0.0.1` by default)
- default GUI role becomes `developer` for low-friction run controls
- default GUI profile hint becomes `local_simple`
- Overview tab has **Quick Run** (one-click `/api/runs/start` execute mode) using local-simple defaults + selected/default PRD path
- Overview also includes the latest run **Scorecard** widget (`/api/scorecard/latest`) for fast quality/status checks
- Overview/Validation/Processes tabs now expose explicit loading/empty/error state boxes with recovery actions (refresh/retry + logs hint shortcuts)
- CLI startup now emits one concise summary block: URL (including `--open` result), kickoff status, and a single next action
- Validation tab includes read-only **Artifact Viewer** (`/api/runs/<run_id>/artifacts/read`) with failed-validator triage deep-link buttons, pretty JSON rendering (raw fallback on malformed/truncated payloads), plus copy/download utilities with auto-clearing success/error toasts and focus preservation
- Processes tab surfaces active/recent tracked processes (`/api/processes`) with run linkage, retry-chain summary, transition history, and stop/retry actions

Local-simple smoke lane (NXT-007):
```bash
# local
make smoke-local-simple-e2e

# CI-equivalent one-shot
python scripts/local_simple_e2e_smoke.py --artifacts-dir ./artifacts/local-simple-e2e-smoke
```
Smoke artifacts are persisted under `./artifacts/local-simple-e2e-smoke/<timestamp>/` (server stdout/stderr logs + API snapshots) so failures remain debuggable.

RC dry-run command (no process spawn):
```bash
curl -fsS -X POST http://127.0.0.1:8787/api/runs/start \
  -H 'Content-Type: application/json' \
  -d '{"prd":"examples/PRD.md","out":"./generated_runs","profile":"local_simple","execute":false}'
```
Expected highlights: `spawned=false`, `result_status=dry_run`.

See `docs/LOCAL_SIMPLE_MODE.md` for local-simple operator quickstart, run controls, and hardened-mode handoff.
For day-1 setup and demo flow, use `docs/onboarding.md` and `docs/DEMO_PLAYBOOK.md`.
For RC handoff artifacts, use `docs/RC_NEXT_CUT_CHECKLIST.md` and `docs/CHANGELOG_DRAFT_NEXT_CUT.md`.

Known limits (MVP):
- Polling-based updates only (no live stream/WebSocket yet).
- Process control is best-effort (depends on tracked process lifecycle).
- JSON artifact schema is not versioned yet; breaking changes may require GUI updates.

Stabilization handoff bundle (active):
- `docs/STATUS_BOARD_CURRENT.md`
- `docs/STABILIZATION_48H_CHECKLIST.md`
- `docs/STABILIZATION_MODE.md`
- `docs/DEMO_PLAYBOOK.md`

Next-wave planning/governance docs:
- `docs/PLAN_NEXT_WEEK.md`
- `docs/BACKLOG_NEXT_WEEK.md`
- `docs/AUTONOMOUS_COMMERCIAL_PLAN.md` (commercial autonomous rollout strategy)

Legacy showoff planning docs (reference/archive):
- `docs/ROADMAP_SHOWOFF.md`
- `docs/BACKLOG_SHOWOFF.md`

## Output Directory Naming
`--out` is treated as a parent directory.
Each run creates a unique child directory:

`<out>/<prd-file-stem>_<YYYYMMDD_HHMMSS>`

Behavior details:
- PRD stem is taken from the input file name (without extension).
- Unsafe characters are replaced with `-`.
- If the same second collides, suffix is appended (`_01`, `_02`, ...).

Example:
- Input PRD: `examples/My cool PRD v2.md`
- Output root: `./generated_runs`
- Run folder: `generated_runs/My-cool-PRD-v2_20260224_153045`

## Run Artifacts
Inside each run folder:
- Generated project files and tests.
- `.autodev/prd_struct.json`: normalized PRD.
- `.autodev/plan.json`: generated task plan.
- `.autodev/checkpoint.json`: task completion checkpoint for `--resume`.
- `.autodev/task_<id>_last_validation.json`: per-task validation snapshots.
- `.autodev/REPORT.md`: final summary with pass/fail status and last validation output.
- `sbom/` artifacts (from `scripts/generate_sbom.py`), including CycloneDX JSON and license reports.

## Supply-chain and dependency lock guidance (templates)
Generated templates now keep pinned dependency locks:
- Runtime/development requirements are pinned in `requirements.txt` and `requirements-dev.txt`.
- Direct lock files are maintained in `requirements.lock` and `requirements-dev.lock`.
- For refresh and verification guidance, see `docs/ops/requirements-locking.md`.

## Validators
Supported validator names:
- `ruff`
- `mypy`
- `pytest`
- `pip_audit`
- `bandit`
- `semgrep`
- `sbom`
- `docker_build`

Notes:
- `pip_audit` can be treated as warning when `audit_required: false`.
- `semgrep` uses local rule file `.semgrep.yml`.
- `docker_build` runs `docker build -t autodev-app:test .`.
- `quality_profile.validator_policy.per_task.soft_fail` controls which validators are non-blocking inside task fix loops.
- `quality_profile.validator_policy.final.soft_fail` controls which validators are non-blocking in final project validation.
- `quality_profile.validator_graph.enabled` defaults to `false`; keep it off until stabilization gates pass.
- `quality_profile.validator_graph.mode` supports `strict` and `relaxed`.
- `quality_profile.validator_graph.skip_on_soft_fail` controls whether soft prerequisite failures can skip dependent validators.
- `quality_profile.validator_graph.custom_edges` adds project-specific dependency edges (`dependent: [prerequisite]`).
- Legacy top-level `validator_policy` is normalized into `quality_profile.validator_policy` for backward compatibility.
- Config load validates validator names and profile structure; invalid entries fail fast with path-specific errors.

## Recommended PRD Structure
The parser accepts free-form markdown, but this structure works best:
- `# Title`
- `## Goals`
- `## Non-Goals`
- `## Personas` (optional)
- `## Features`
- `## Acceptance Criteria`
- `## Non-Functional Requirements`
- `## Constraints` (optional)

See `examples/PRD.md` for a concrete input.

## Key Modules
- `autodev/main.py`: CLI entrypoint and run directory resolution.
- `autodev/loop.py`: orchestration loop (plan, implement, validate, fix).
- `autodev/roles.py`: LLM role prompts.
- `autodev/schemas.py`: JSON schemas for PRD, plan, and changesets.
- `autodev/validators.py`: validation command wiring.
- `autodev/exec_kernel.py`: allowlisted command runner.
- `autodev/workspace.py`: safe file operations and patch application.

## Security Boundaries
- No `shell=True`; commands are executed as argv lists.
- Only allowlisted python modules/scripts and select external tools are executable.
- Workspace file operations prevent escaping the run root.

## Known Limits
- Current planner supports two template families: `python_fastapi`, `python_cli`.
- Quality depends on PRD clarity and model quality.
- Some checks require network or local tool availability (`pip_audit`, Docker).
