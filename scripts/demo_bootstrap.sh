#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

info() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*"
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

python_is_311_plus() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

python_has_module() {
  "$1" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$2') else 1)" >/dev/null 2>&1
}

run_safe_fallback_checks() {
  local py_exec="$1"
  warn "Running safe fallback checks with $py_exec"
  "$py_exec" -m compileall -q autodev
  if python_has_module "$py_exec" ruff; then
    "$py_exec" -m ruff check autodev
  else
    warn "ruff is not installed; skipping lint check in fallback mode."
  fi
  if python_has_module "$py_exec" pytest; then
    "$py_exec" -m pytest -q autodev/tests
  else
    warn "pytest is not installed; skipping test check in fallback mode."
  fi
}

find_python_311_plus() {
  local candidate
  local -a commands=(python3.11 python3 python)

  for candidate in "${commands[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_311_plus "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  local -a detected=()
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      detected+=("$candidate=$($candidate -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo unknown)")
    fi
  done

  if [ "${#detected[@]}" -eq 0 ]; then
    fail "Python 3.11+ is required, but no Python interpreter was found in PATH."
  fi

  fail "Python 3.11+ is required. Found: ${detected[*]}. Please install Python 3.11 or newer."
}

has_make_target() {
  local target="$1"
  [ -f "$ROOT_DIR/Makefile" ] && grep -qE "^${target}:" "$ROOT_DIR/Makefile"
}

main() {
  cd "$ROOT_DIR"
  info "Repo root: $ROOT_DIR"

  local py_cmd
  py_cmd="$(find_python_311_plus)"
  local py_ver
  py_ver="$($py_cmd -c 'import sys; print(sys.version.split()[0])')"
  info "Using Python interpreter: $py_cmd ($py_ver)"

  if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment at $VENV_DIR"
    "$py_cmd" -m venv "$VENV_DIR"
  else
    info "Virtual environment already exists at $VENV_DIR (reusing)"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  local venv_python="$VENV_DIR/bin/python"

  [ -f "$ROOT_DIR/requirements.txt" ] || fail "requirements.txt not found at repo root: $ROOT_DIR"
  info "Installing dependencies from requirements.txt"
  "$venv_python" -m pip install --upgrade pip >/dev/null
  "$venv_python" -m pip install -r requirements.txt

  if [ -f "$ROOT_DIR/requirements-dev.txt" ]; then
    info "Installing dev tooling from requirements-dev.txt (for ci-fast health checks)"
    "$venv_python" -m pip install -r requirements-dev.txt
  fi

  info "Health check: python -m autodev.main --help"
  "$venv_python" -m autodev.main --help >/dev/null

  if command -v make >/dev/null 2>&1 && has_make_target "ci-fast"; then
    if command -v python3.11 >/dev/null 2>&1 && python_is_311_plus python3.11; then
      info "Health check: make PYTHON=python3.11 ci-fast"
      if ! make PYTHON=python3.11 ci-fast; then
        warn "make ci-fast failed with python3.11; falling back to safe local checks."
        run_safe_fallback_checks "$venv_python"
      fi
    else
      warn "ci-fast target exists, but python3.11 is unavailable. Falling back to safe local checks."
      run_safe_fallback_checks "$venv_python"
    fi
  else
    warn "make or ci-fast target not available. Running safe fallback checks."
    run_safe_fallback_checks "$venv_python"
  fi

  cat <<EOF

✅ Demo bootstrap complete.

Next steps:
  1) Activate environment:
     source "$VENV_DIR/bin/activate"

  2) Verify CLI:
     python -m autodev.main --help

  3) Run demo PRD:
     autodev --prd examples/PRD.md --out ./generated_runs --profile enterprise

EOF
}

main "$@"
