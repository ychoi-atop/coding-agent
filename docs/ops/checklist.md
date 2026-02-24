# Template CI Drift Checklist

Machine-checkable policy checks are defined in:
- `docs/ops/template-validation-contract.json`
- `docs/ops/check_template_parity_audit.py`
- `docs/ops/check_template_ci_drift.sh`
- `docs/ops/check_template_dependency_locks.sh`

Run from repository root:

```bash
bash docs/ops/check_template_parity_audit.sh
bash docs/ops/check_template_dependency_locks.sh
```

Optional: target specific CI workflows for drift validation:

```bash
bash docs/ops/check_template_ci_drift.sh . "templates/python_fastapi/.github/workflows/ci.yml"
bash docs/ops/check_template_ci_drift.sh . "templates/python_cli/.github/workflows/ci.yml"
```

Required pre-merge checks:

```bash
bash docs/ops/check_template_parity_audit.sh
bash docs/ops/check_template_ci_drift.sh
bash docs/ops/check_template_dependency_locks.sh
```

This is exactly what `.github/workflows/template-governance.yml` runs on `push` and `pull_request`.

## Periodic maintenance (dependency governance)

From the repository root, use the new make targets for repeatable maintenance checks:

```bash
make compile
make check
make tests
make check-template
make check-locks
make ci
```

Suggested cadence:
- **Daily/Per PR branch pull**: `make compile`, `make check`, `make tests`
- **Weekly maintenance**: `make ci` (full bundle: compile + checks + tests + template/lock checks)
- **After dependency/template changes**: `make check-template` and `make check-locks`
