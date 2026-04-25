#!/usr/bin/env bash
# Start local Strategy Lab UI (paper-only; no TWS/IBKR on startup; no broker orders).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
bash "$ROOT/scripts/start_strategy_lab_ui.sh"
bash "$ROOT/scripts/open_strategy_lab_ui.sh" || true
echo ""
echo "---"
echo "Logs: ${ROOT}/logs/  —  Press Enter to close this window."
read -r
