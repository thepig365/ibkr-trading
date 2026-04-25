#!/usr/bin/env bash
# Show UI process, URL, healthz, and recent log tails.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
PORT="${STRATEGY_LAB_PORT:-8765}"
URL="http://${HOST}:${PORT}/"
HEALTH="http://${HOST}:${PORT}/healthz"

PID_FILE="${ROOT}/data/runtime/strategy_lab_ui.pid"
OUT_LOG="${ROOT}/logs/strategy-lab-ui.stdout.log"
ERR_LOG="${ROOT}/logs/strategy-lab-ui.stderr.log"

if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d ' \n' < "$PID_FILE" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "UI status: RUNNING"
    echo "  pid:      $pid"
  else
    echo "UI status: NOT RUNNING (stale pid file?)"
  fi
else
  echo "UI status: NOT RUNNING (no pid file)"
  pid=""
fi
echo "  url:      $URL"

if command -v curl >/dev/null 2>&1; then
  echo "  healthz:  (curl ${HEALTH})"
  curl -sS --connect-timeout 2 "$HEALTH" 2>&1 | head -c 400 || true
  echo
else
  echo "  healthz:  (install curl to probe)"
fi

TAILN="${LOG_TAIL_LINES:-12}"
if [[ -f "$OUT_LOG" ]]; then
  echo "--- last ${TAILN} lines: logs/strategy-lab-ui.stdout.log ---"
  tail -n "$TAILN" "$OUT_LOG" 2>/dev/null || true
fi
if [[ -f "$ERR_LOG" ]]; then
  echo "--- last ${TAILN} lines: logs/strategy-lab-ui.stderr.log ---"
  tail -n "$TAILN" "$ERR_LOG" 2>/dev/null || true
fi
exit 0
