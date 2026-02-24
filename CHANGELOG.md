# Changelog

## [Unreleased] coding-agent stabilization pass

### Added
- Added root-level quality gate CI workflow:
  - `/.github/workflows/ci.yml`
  - `requirements-dev.txt` for shared quality tools
- Added shared template CI workflow:
  - `templates/_shared/ci/ci.yml`
  - `templates/python_fastapi/.github/workflows/ci.yml` and `templates/python_cli/.github/workflows/ci.yml` now reuse shared CI
- Added governance and maintenance automation:
  - `Makefile` with targets: `compile`, `check`, `tests`, `ci`, `check-template`, `check-locks`
  - `docs/ops/check_template_dependency_locks.sh`
  - `docs/ops/check_template_ci_drift.sh`
  - `docs/ops/check_template_parity_audit.py`
  - `docs/ops/check_template_parity_audit.sh`
  - `.github/workflows/template-governance.yml`
- Added supply-chain governance docs:
  - `docs/ops/requirements-locking.md`
- Added operations docs:
  - `docs/deployment.md`
  - `docs/monitoring.md`
  - `docs/failure-handling.md`
  - `docs/ops/checklist.md`
- Added parity test and audit coverage:
  - `autodev/tests/test_template_parity_audit.py`
- Added template dependency lock files:
  - `templates/python_fastapi/requirements.lock`
  - `templates/python_fastapi/requirements-dev.lock`
  - `templates/python_cli/requirements.lock`
  - `templates/python_cli/requirements-dev.lock`
  - `templates/python_library/requirements.lock`
  - `templates/python_library/requirements-dev.lock`

### Changed
- Core config/profile behavior:
  - `autodev/config.py`: explicit profile validation and required keys, ambiguity handling, `disable_docker_build`, and default injection for `security`/`quality_profile`
  - `autodev/main.py`: explicit profile resolution and metadata propagation (`run_id`, `request_id`)
- Core reliability hardening:
  - `autodev/loop.py`: `_toposort()` now fail-fast on dependency cycles with actionable diagnostics
  - `autodev/env_manager.py`: bootstrap/install now raise on non-zero return codes
- Validator architecture & policy:
  - `autodev/validators.py`: registry-based dispatch, `dependency_lock` validator, structured events and tool-unavailable classification
  - `autodev/schemas.py`: validator list aligned with runtime registry
- Observability:
  - `autodev/main.py`, `autodev/loop.py`, `autodev/validators.py`: structured event logging with `run_id`, `request_id`, `profile`, `task_id`, iteration
  - Added `.autodev/run_metadata.json` metadata persistence
- Patch safety and rollback:
  - `autodev/patch_utils.py`: dry-run patch validation mode and stricter fallback handling
  - `autodev/workspace.py`: two-phase apply with validation + rollback
- Template security hardening:
  - `autodev/exec_kernel.py`: Dockerfile policy checks + strict-mode override support
  - `templates/python_fastapi/Dockerfile`, `templates/python_cli/Dockerfile`: non-root runtime execution and ownership hardening
- Dependency governance:
  - `templates/python_fastapi/requirements*.txt`
  - `templates/python_cli/requirements*.txt`
  - `templates/python_library/requirements-dev.txt`
  - Updated to pinned versions and synchronized lock workflow

### Fixed
- Prevented silent or ambiguous runtime failure modes:
  - Profile ambiguity and implicit defaults
  - Bootstrap and install failures being hidden
  - Partial patch application side-effects
  - Template CI drift against governance contract
- Fixed secret handling posture by shifting API key resolution to env-driven flow (`AUTODEV_LLM_API_KEY`) and avoiding plaintext dependency on config defaults
- Added explicit checks for lock and parity compliance to prevent template/workflow drift

### Testing
- Added/updated tests:
  - `autodev/tests/test_config.py`
  - `autodev/tests/test_main.py`
  - `autodev/tests/test_loop.py`
  - `autodev/tests/test_env_manager.py`
  - `autodev/tests/test_exec_kernel.py`
  - `autodev/tests/test_patch_utils.py`
  - `autodev/tests/test_workspace_patch.py`
  - `autodev/tests/test_validators.py`
  - `autodev/tests/test_template_parity_audit.py`
- Validation commands passing:
  - `bash docs/ops/check_template_dependency_locks.sh`
  - `bash docs/ops/check_template_ci_drift.sh`
  - `python3 docs/ops/check_template_parity_audit.py`
  - `make check-template`
  - `make check-locks`
  - `make check` (PASS)

### Maintenance
- Type-check cleanup completed:
  - `autodev/__init__.py`
  - `autodev/config.py`
  - `autodev/loop.py`
  - `autodev/workspace.py`
  - `autodev/tests/test_schemas.py`
- Addressed mypy blockers with minimal type-focused fixes to restore full check pass
