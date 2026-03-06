#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="127.0.0.1"
PORT="8787"
RUNS_ROOT="${1:-./generated_runs}"
BASE_URL="http://${HOST}:${PORT}"

cleanup() {
  if [[ -n "${GUI_PID:-}" ]] && kill -0 "$GUI_PID" 2>/dev/null; then
    kill "$GUI_PID" 2>/dev/null || true
    wait "$GUI_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ ! -d "$RUNS_ROOT" ]]; then
  echo "[showoff-smoke] runs root not found: $RUNS_ROOT"
  echo "[showoff-smoke] generating demo fixtures..."
  python3 scripts/showoff_seed_fixtures.py
fi

echo "[showoff-smoke] starting GUI server on ${BASE_URL}"
if command -v autodev >/dev/null 2>&1; then
  autodev gui --runs-root "$RUNS_ROOT" --host "$HOST" --port "$PORT" >/tmp/showoff_gui.log 2>&1 &
else
  python3 -m autodev.main gui --runs-root "$RUNS_ROOT" --host "$HOST" --port "$PORT" >/tmp/showoff_gui.log 2>&1 &
fi
GUI_PID=$!

for _ in {1..20}; do
  if curl -fsS "${BASE_URL}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! curl -fsS "${BASE_URL}/healthz" >/dev/null; then
  echo "[showoff-smoke] health check failed"
  echo "---- gui log ----"
  cat /tmp/showoff_gui.log || true
  exit 1
fi

echo "[showoff-smoke] /healthz OK"

RUNS_JSON="$(curl -fsS "${BASE_URL}/api/runs")"
echo "$RUNS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); runs=d.get("runs", []); assert isinstance(runs, list); print("[showoff-smoke] runs=%d" % len(runs))'

FIRST_RUN_ID="$(echo "$RUNS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); runs=d.get("runs",[]); print((runs[0].get("run_id", "") if runs else ""))')"
if [[ -n "$FIRST_RUN_ID" ]]; then
  curl -fsS "${BASE_URL}/api/runs/${FIRST_RUN_ID}" >/dev/null
  echo "[showoff-smoke] detail endpoint OK for run_id=${FIRST_RUN_ID}"
else
  echo "[showoff-smoke] warning: no runs available for detail check"
fi

echo "[showoff-smoke] PASS"
