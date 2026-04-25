#!/usr/bin/env bash
# Start local Strategy Lab UI (FastAPI). Paper-only — no IBKR/TWS at startup.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export STRATEGY_LAB_HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
export STRATEGY_LAB_PORT="${STRATEGY_LAB_PORT:-8765}"
RUNTIME_DIR="${ROOT}/data/runtime"
LOG_DIR="${ROOT}/logs"
PID_FILE="${RUNTIME_DIR}/strategy_lab_ui.pid"
OUT_LOG="${LOG_DIR}/strategy-lab-ui.stdout.log"
ERR_LOG="${LOG_DIR}/strategy-lab-ui.stderr.log"

if [[ -f "${ROOT}/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "${ROOT}/.venv/bin/activate"
fi

PYTHON="${PYTHON:-python3}"
URL="http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/"

_is_running() {
  local p="$1"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null
}

if [[ -f "$PID_FILE" ]]; then
  old="$(tr -d ' \n' < "$PID_FILE" || true)"
  if _is_running "$old"; then
    echo "Strategy Lab UI already running. Open: $URL (pid $old)" >&2
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
: >>"$OUT_LOG" >>"$ERR_LOG"

# Bind loopback; never default to 0.0.0.0
nohup $PYTHON -m bot_ui --host 127.0.0.1 --port "$STRATEGY_LAB_PORT" \
  >>"$OUT_LOG" 2>>"$ERR_LOG" &
echo $! >"$PID_FILE"
echo "Started Strategy Lab UI pid=$(cat "$PID_FILE")" >&2
echo "URL: $URL" >&2
echo "logs: $OUT_LOG / $ERR_LOG" >&2
exit 0
