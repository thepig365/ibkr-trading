#!/usr/bin/env bash
# Stop Strategy Lab UI only (TWS and paper loops are not touched).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="${ROOT}/data/runtime/strategy_lab_ui.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No UI PID file at $PID_FILE (nothing to stop)."
  exit 0
fi

pid="$(tr -d ' \n' < "$PID_FILE" || true)"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Stopping Strategy Lab UI pid=$pid ..." >&2
  kill "$pid" 2>/dev/null || true
  sleep 0.4
  if kill -0 "$pid" 2>/dev/null; then
    echo "Process still up; send SIGTERM again or kill -9 $pid" >&2
  fi
else
  echo "Stale PID file (not running). Removing." >&2
fi
rm -f "$PID_FILE"
echo "Done." >&2
exit 0
