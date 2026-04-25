#!/usr/bin/env bash
# Strategy Lab (local) — start / stop / status for the FastAPI UI.
# Paper-only: never places orders, never enables live trading.
# PID + log go under data/runtime/ (gitignored; not committed).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUNTIME_DIR="${ROOT}/data/runtime"
PID_FILE="${RUNTIME_DIR}/strategy-lab-ui.pid"
LOG_FILE="${RUNTIME_DIR}/strategy-lab-ui.log"
export STRATEGY_LAB_HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
export STRATEGY_LAB_PORT="${STRATEGY_LAB_PORT:-8765}"

py="${PYTHON:-python3}"

usage() {
  echo "Usage: $0 {start|stop|status|restart}" >&2
  echo "  start   — background: python -m bot_ui (log: data/runtime/strategy-lab-ui.log)" >&2
  echo "  stop    — stop using PID file" >&2
  echo "  status  — show PID, curl /healthz if up, and engine JSON from CLI" >&2
  echo "  restart — stop then start" >&2
  exit 1
}

_ensure_runtime_dir() {
  mkdir -p "$RUNTIME_DIR"
}

_is_pid_running() {
  local p="$1"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null
}

cmd_start() {
  _ensure_runtime_dir
  if [[ -f "$PID_FILE" ]]; then
    local old
    old="$(tr -d ' \n' < "$PID_FILE" || true)"
    if _is_pid_running "$old"; then
      echo "Strategy Lab UI already running (pid $old, port ${STRATEGY_LAB_PORT})." >&2
      return 0
    fi
  fi
  nohup ${py} -m bot_ui --host "$STRATEGY_LAB_HOST" --port "$STRATEGY_LAB_PORT" \
    >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "Started Strategy Lab UI pid=$(cat "$PID_FILE")  http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/"
  echo "Log: $LOG_FILE"
}

cmd_stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file; UI not started via this script ($PID_FILE)." >&2
    return 0
  fi
  local p
  p="$(tr -d ' \n' < "$PID_FILE" || true)"
  if _is_pid_running "$p"; then
    echo "Stopping pid $p ..."
    kill "$p" 2>/dev/null || true
    sleep 0.3
  else
    echo "Stale PID file (not running). Removing $PID_FILE" >&2
  fi
  rm -f "$PID_FILE"
  echo "Stopped."
}

cmd_status() {
  if [[ -f "$PID_FILE" ]]; then
    local p
    p="$(tr -d ' \n' < "$PID_FILE" || true)"
    if _is_pid_running "$p"; then
      echo "ui_process: running pid=$p"
      if command -v curl >/dev/null 2>&1; then
        echo "healthz:"
        curl -sS "http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/healthz" || true
        echo
      else
        echo "healthz: (install curl to probe /healthz)"
      fi
    else
      echo "ui_process: not_running (stale pid file)"
    fi
  else
    echo "ui_process: not_running (no pid file; use '$0 start' for background, or: python3 -m bot_ui)"
  fi
  echo "--- engine (read-only, no IBKR) ---"
  ${py} -m bot.cli strategy-lab-engine-status --json
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart) cmd_stop; cmd_start ;;
  *) usage ;;
esac
