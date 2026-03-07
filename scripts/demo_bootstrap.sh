#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
HOST="127.0.0.1"
PORT="8787"
RUNS_ROOT="./generated_runs"
CHECK_ONLY=1
OPEN_BROWSER=0
LOG_FILE="$ROOT_DIR/artifacts/demo-bootstrap/local-simple.log"

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

usage() {
  cat <<EOF
Usage: bash scripts/demo_bootstrap.sh [options]

One-command demo bootstrap for local-simple mode:
  1) verify Python 3.11+
  2) install deps into .venv (idempotent)
  3) seed deterministic fixtures
  4) launch local-simple + run health/API checks

Options:
  --serve                  Keep local-simple running after checks
  --open                   Pass --open to local-simple (best-effort browser open)
  --host <HOST>            Bind host (default: 127.0.0.1)
  --port <PORT>            Bind port (default: 8787)
  --runs-root <PATH>       Runs root (default: ./generated_runs)
  --venv-dir <PATH>        Virtualenv path (default: ./.venv)
  --log-file <PATH>        Server log path (default: artifacts/demo-bootstrap/local-simple.log)
  -h, --help               Show this help

Examples:
  bash scripts/demo_bootstrap.sh
  bash scripts/demo_bootstrap.sh --serve --open
EOF
}

python_is_311_plus() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
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

cleanup() {
  if [[ -n "${GUI_PID:-}" ]] && kill -0 "$GUI_PID" 2>/dev/null; then
    kill "$GUI_PID" 2>/dev/null || true
    wait "$GUI_PID" 2>/dev/null || true
  fi
}

wait_for_health() {
  local base_url="$1"
  local timeout_sec="${2:-30}"
  local started_at
  started_at="$(date +%s)"

  while true; do
    if curl -fsS "${base_url}/healthz" >/tmp/demo_bootstrap_healthz.json 2>/dev/null; then
      if python3 - <<'PY' /tmp/demo_bootstrap_healthz.json
import json, sys
payload = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
raise SystemExit(0 if payload.get('ok') is True else 1)
PY
      then
        return 0
      fi
    fi

    if (( "$(date +%s)" - started_at >= timeout_sec )); then
      return 1
    fi
    sleep 0.5
  done
}

ensure_endpoints() {
  local base_url="$1"

  curl -fsS "${base_url}/api/runs" >/tmp/demo_bootstrap_runs.json
  python3 - <<'PY' /tmp/demo_bootstrap_runs.json
import json, sys
payload = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
runs = payload.get('runs', [])
if not isinstance(runs, list):
    raise SystemExit('runs field is not a list')
if len(runs) == 0:
    raise SystemExit('runs list is empty (fixtures may not have been seeded)')
print(f"[INFO] /api/runs count={len(runs)}")
PY

  curl -fsS "${base_url}/api/gui/context" >/tmp/demo_bootstrap_context.json
  python3 - <<'PY' /tmp/demo_bootstrap_context.json
import json, sys
payload = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
mode = payload.get('mode')
if mode not in {'local-simple', 'local_simple'}:
    raise SystemExit(f"unexpected mode in /api/gui/context: {mode!r}")
print(f"[INFO] /api/gui/context mode={mode}")
PY
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --serve)
        CHECK_ONLY=0
        shift
        ;;
      --open)
        OPEN_BROWSER=1
        shift
        ;;
      --host)
        HOST="$2"
        shift 2
        ;;
      --port)
        PORT="$2"
        shift 2
        ;;
      --runs-root)
        RUNS_ROOT="$2"
        shift 2
        ;;
      --venv-dir)
        VENV_DIR="$2"
        shift 2
        ;;
      --log-file)
        LOG_FILE="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1 (use --help)"
        ;;
    esac
  done
}

main() {
  parse_args "$@"

  cd "$ROOT_DIR"
  mkdir -p "$(dirname "$LOG_FILE")"

  info "Repo root: $ROOT_DIR"
  info "Mode: $([ "$CHECK_ONLY" -eq 1 ] && echo "bootstrap-check" || echo "bootstrap-serve")"

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
  "$venv_python" -m pip install -r requirements.txt >/dev/null

  if [ -f "$ROOT_DIR/requirements-dev.txt" ]; then
    info "Installing dev tooling from requirements-dev.txt"
    "$venv_python" -m pip install -r requirements-dev.txt >/dev/null
  fi

  info "Seeding deterministic fixtures into $RUNS_ROOT"
  "$venv_python" scripts/showoff_seed_fixtures.py --clean --root "$RUNS_ROOT"

  local base_url="http://${HOST}:${PORT}"
  local -a launch_cmd=("$venv_python" -m autodev.main local-simple --runs-root "$RUNS_ROOT" --host "$HOST" --port "$PORT")
  if [ "$OPEN_BROWSER" -eq 1 ]; then
    launch_cmd+=(--open)
  fi

  trap cleanup EXIT

  info "Launching local-simple: ${launch_cmd[*]}"
  "${launch_cmd[@]}" >"$LOG_FILE" 2>&1 &
  GUI_PID=$!

  if ! wait_for_health "$base_url" 30; then
    warn "local-simple failed health check within timeout. Last 80 log lines:"
    tail -n 80 "$LOG_FILE" || true
    fail "bootstrap failed: /healthz did not report ok=true"
  fi

  ensure_endpoints "$base_url"

  cat <<EOF

✅ Demo bootstrap checks passed.

Sanity checks completed:
  - fixture seed: OK ($RUNS_ROOT)
  - local-simple launch: OK ($base_url)
  - endpoint checks: /healthz, /api/runs, /api/gui/context

Launcher command:
  source "$VENV_DIR/bin/activate"
  autodev local-simple --runs-root "$RUNS_ROOT" --host "$HOST" --port "$PORT"$([ "$OPEN_BROWSER" -eq 1 ] && echo " --open")

Server log:
  $LOG_FILE

EOF

  if [ "$CHECK_ONLY" -eq 1 ]; then
    info "Check-only mode: stopping local-simple after successful sanity checks."
    return 0
  fi

  info "Serve mode: local-simple is running at $base_url (PID: $GUI_PID). Press Ctrl+C to stop."
  wait "$GUI_PID"
}

main "$@"
