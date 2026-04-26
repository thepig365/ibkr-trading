#!/usr/bin/env bash
# Open the Strategy Lab URL in the default browser (macOS) or xdg-open (Linux).
# If the PID file is missing but /healthz responds, still open (e.g. after manual
# start or a recovered process). Use STRATEGY_LAB_UI_PATH (default /) for the path.
set -euo pipefail

HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
PORT="${STRATEGY_LAB_PORT:-8765}"
UI_PATH="${STRATEGY_LAB_UI_PATH:-/}"
[[ "$UI_PATH" == /* ]] || UI_PATH="/$UI_PATH"
URL="http://${HOST}:${PORT}${UI_PATH}"
HEALTH="http://${HOST}:${PORT}/healthz"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="${ROOT}/data/runtime/strategy_lab_ui.pid"
running=0
if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d ' \n' < "$PID_FILE" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    running=1
  fi
fi

if [[ "$running" -eq 0 ]]; then
  if command -v curl >/dev/null 2>&1; then
    if curl -sf --connect-timeout 2 --max-time 5 "$HEALTH" 2>/dev/null | grep -q '"status":"ok"'; then
      running=1
    fi
  fi
fi

if [[ "$running" -eq 0 ]]; then
  echo "Strategy Lab UI does not appear to be running (no live PID in $PID_FILE, healthz not ok)." >&2
  echo "Start with:  ./scripts/start_strategy_lab_ui.sh" >&2
  echo "  or:         python3 -m bot_ui" >&2
  echo "  or:         open Strategy Lab.command   (one-click: start if needed, then open)" >&2
  exit 1
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  open "$URL"
else
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  else
    echo "Open in browser: $URL"
  fi
fi
exit 0
