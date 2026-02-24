#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(pwd)}"
TEMPLATES_DIR="$ROOT_DIR/templates"

if [ ! -d "$TEMPLATES_DIR" ]; then
  echo "[ERROR] templates directory not found: $TEMPLATES_DIR"
  exit 1
fi

python3 - "$ROOT_DIR" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
templates_dir = root / "templates"
errors = False


def parse_req_specs(path: Path):
    req_specs = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", "--")):
            continue
        normalized = line.split("#", 1)[0].strip()
        if not normalized:
            continue
        if "==" not in normalized:
            print(f"[ERROR] {path}: line {line_no}: unsupported requirement specifier '{line}'. expected pinned 'name==version'")
            return None
        name, version = normalized.split("==", 1)
        req_specs.append(f"{name.strip()}=={version.strip()}")
    return req_specs


def parse_lock_specs(path: Path):
    lock_specs = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split()[0]
        if "==" not in token:
            continue
        lock_specs.append(token.split(" ", 1)[0])
    return lock_specs

for tpl in sorted(p for p in templates_dir.iterdir() if p.is_dir() and p.name != "_shared"):
    for req_name in ("requirements.txt", "requirements-dev.txt"):
        req_file = tpl / req_name
        if not req_file.is_file():
            continue

        # requirements.txt -> requirements.lock
        # requirements-fastapi-dev.txt -> requirements-dev.lock
        lock_file = req_file.with_suffix(".lock")

        if not lock_file.is_file():
            print(f"[ERROR] Missing lock file for {req_file}: {lock_file}")
            errors = True
            continue

        req_specs = parse_req_specs(req_file)
        if req_specs is None:
            errors = True
            continue

        lock_specs = parse_lock_specs(lock_file)

        req_set = sorted(set(req_specs))
        lock_set = sorted(set(lock_specs))
        if req_set != lock_set:
            missing_in_lock = [spec for spec in req_set if spec not in lock_set]
            extra_in_lock = [spec for spec in lock_set if spec not in req_set]
            if missing_in_lock:
                print(f"[ERROR] {req_file}: lock file missing entries: {', '.join(missing_in_lock)}")
            if extra_in_lock:
                print(f"[ERROR] {req_file}: lock file has extra entries: {', '.join(extra_in_lock)}")
            errors = True

if errors:
    sys.exit(1)

print("Template dependency lock files are present and aligned with requirements.txt / requirements-dev.txt.")
PY
