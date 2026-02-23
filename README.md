# AutoDev Agent (Enterprise+)

AutoDev Agent is a PRD-to-code orchestration tool.
It takes a Markdown PRD, plans implementation tasks, generates code, runs local validation, and auto-fixes failures in bounded loops.

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

## Requirements
- Python 3.10+
- Optional Docker (only needed for `docker_build` validation)
- OpenAI-compatible chat endpoint (LM Studio or OpenAI-compatible API)

## Install
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

```yaml
llm:
  base_url: "http://127.0.0.1:1234/v1"
  api_key: "lm-studio"
  model: "qwen3-coder-30b-a3b-instruct-mlx"
  timeout_sec: 240

run:
  max_json_repair: 2
  max_fix_loops_total: 10
  max_fix_loops_per_task: 4
  verbose: true

profiles:
  enterprise:
    template_candidates: ["python_fastapi", "python_cli"]
    validators: ["ruff", "mypy", "pytest", "pip_audit", "bandit", "sbom"]
    security:
      audit_required: false
```

## Run
Use either module form:
```bash
python -m autodev.main --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

Or installed script form:
```bash
autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise
```

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
- `.autodev/task_<id>_last_validation.json`: per-task validation snapshots.
- `.autodev/REPORT.md`: final summary with pass/fail status and last validation output.
- `sbom/` artifacts (from `scripts/generate_sbom.py`), including CycloneDX JSON and license reports.

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
