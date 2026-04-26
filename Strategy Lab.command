#!/usr/bin/env bash
# One-click Strategy Lab: start the local UI if needed, then open the dashboard in the browser.
# Paper-only; no IBKR/TWS, no orders, no automated paper pass, no auto trading loop.
set -euo pipefail

# Repo root = this file's directory (safe with spaces in path)
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export STRATEGY_LAB_HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
export STRATEGY_LAB_PORT="${STRATEGY_LAB_PORT:-8765}"
HEALTH="http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/healthz"
OUT_LOG="${ROOT}/logs/strategy-lab-ui.stdout.log"
ERR_LOG="${ROOT}/logs/strategy-lab-ui.stderr.log"

_health_ok() {
  command -v curl >/dev/null 2>&1 || return 1
  curl -sf --connect-timeout 2 --max-time 5 "$HEALTH" 2>/dev/null | grep -q '"status":"ok"'
}

_open_dashboard() {
  export STRATEGY_LAB_UI_PATH=/dashboard
  bash "$ROOT/scripts/open_strategy_lab_ui.sh"
}

if _health_ok; then
  echo "Strategy Lab is already running. Opening dashboard." >&2
  _open_dashboard
  exit 0
fi

if ! bash "$ROOT/scripts/start_strategy_lab_ui.sh"; then
  echo "ERROR: start_strategy_lab_ui.sh failed." >&2
  echo "See logs:" >&2
  echo "  $OUT_LOG" >&2
  echo "  $ERR_LOG" >&2
  exit 1
fi

# Wait for /healthz (server can take a moment after nohup)
tries=0
max=80
while ! _health_ok; do
  tries=$((tries + 1))
  if [[ "$tries" -ge "$max" ]]; then
    echo "ERROR: Strategy Lab did not respond on healthz within $(( max / 2 ))s." >&2
    echo "See logs:" >&2
    echo "  $OUT_LOG" >&2
    echo "  $ERR_LOG" >&2
    exit 1
  fi
  sleep 0.5
done

echo "Strategy Lab started. Opening dashboard." >&2
_open_dashboard
exit 0
