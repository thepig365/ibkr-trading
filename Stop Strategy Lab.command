#!/usr/bin/env bash
# Stop the local Strategy Lab web UI process only. Does not stop TWS, paper loops, or TWS/IB Gateway.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
bash "$ROOT/scripts/stop_strategy_lab_ui.sh"
echo ""
echo "Press Enter to close this window."
read -r
