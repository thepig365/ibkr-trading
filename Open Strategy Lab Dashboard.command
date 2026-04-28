#!/usr/bin/env bash
# Opens the dashboard URL only; does NOT start the UI server (use Start Strategy Lab or Strategy Lab.command first).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export STRATEGY_LAB_HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
export STRATEGY_LAB_PORT="${STRATEGY_LAB_PORT:-8765}"
DASH="http://${STRATEGY_LAB_HOST}:${STRATEGY_LAB_PORT}/dashboard"

if open "$DASH" 2>/dev/null; then
  echo "Opening: $DASH"
else
  echo "Open this URL in your browser: $DASH"
fi
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
