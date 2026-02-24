# Generated FastAPI App (AutoDev)

Template CI contract and pinned tool versions live in:
`docs/ops/template-validation-contract.json`.

Run locally:
```bash
python -m pip install -r requirements.txt
# Optional hash verification path (direct deps):
python -m pip install --require-hashes -r requirements.lock --dry-run
PYTHONPATH=src uvicorn app.main:app --reload
```

Test:
```bash
python -m pytest -q
```

Semgrep:
```bash
semgrep --config .semgrep.yml --error
```

SBOM:
```bash
python scripts/generate_sbom.py
ls sbom/
```

## Operational references
- [Onboarding runbook](docs/onboarding.md)
- [Deployment runbook](docs/deployment.md)
- [Monitoring runbook](docs/monitoring.md)
- [Failure handling runbook](docs/failure-handling.md)
