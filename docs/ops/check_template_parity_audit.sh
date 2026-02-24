#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(pwd)}"

if [ ! -f "$ROOT_DIR/docs/ops/check_template_parity_audit.py" ]; then
  echo "[ERROR] parity audit script missing at $ROOT_DIR/docs/ops/check_template_parity_audit.py"
  exit 1
fi

python3 "$ROOT_DIR/docs/ops/check_template_parity_audit.py" "$ROOT_DIR"
