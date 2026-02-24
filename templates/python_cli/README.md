# Generated CLI App (AutoDev)

Template CI contract and pinned tool versions live in:
`docs/ops/template-validation-contract.json`.

Run:
```bash
python -m pip install -r requirements.txt
python -m app.cli --hello world
python -m app.cli --hello world --repeat 2
python -m app.cli --hello world --json
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
```

## Operational references
- [Onboarding runbook](docs/onboarding.md)
- [Deployment runbook](docs/deployment.md)
- [Monitoring runbook](docs/monitoring.md)
- [Failure handling runbook](docs/failure-handling.md)
