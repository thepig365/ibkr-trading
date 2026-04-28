#!/usr/bin/env bash
# Start local Strategy Lab UI (paper-only). Repo root = directory of this file (works after moving the repo).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export STRATEGY_LAB_HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
export STRATEGY_LAB_PORT="${STRATEGY_LAB_PORT:-8765}"
HEALTH="http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/healthz"
DASH="http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/dashboard"

bash "$ROOT/scripts/start_strategy_lab_ui.sh"

# Startup is asynchronous (nohup); wait briefly until /healthz is OK before opening browser.
if command -v curl >/dev/null 2>&1; then
  for ((i = 1; i <= 60; i++)); do
    if curl -sf --connect-timeout 1 --max-time 3 "$HEALTH" 2>/dev/null | grep -q '"status":"ok"'; then
      break
    fi
    sleep 0.5
  done
fi

if ! open "$DASH" 2>/dev/null; then
  echo "(Could not run 'open' for the default browser; use the URL below.)"
fi

echo ""
echo "Strategy Lab UI should be available at:"
echo "$DASH"
echo ""
echo "If the browser did not open automatically, paste the URL above into Safari/Chrome."
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
