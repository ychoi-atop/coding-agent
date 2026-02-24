# Generated Python Library (AutoDev)

## Package

- Source: `src/library`
- Contract: `contracts/library_contract.json`

## Commands

```bash
python -m pip install -r requirements.txt
# Optional hash verification path (direct deps):
python -m pip install --require-hashes -r requirements.lock --dry-run
python -m pytest -q
python -m ruff check src tests
python -m mypy src
semgrep --config .semgrep.yml --error
python scripts/generate_sbom.py
```
