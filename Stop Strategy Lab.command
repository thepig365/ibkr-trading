#!/usr/bin/env bash
# Stop the local Strategy Lab web UI only (repo-relative). Does not stop TWS or paper engines.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

bash "$ROOT/scripts/stop_strategy_lab_ui.sh"

echo ""
echo "Strategy Lab UI stopped."
echo ""
read -n 1 -s -r -p "Press any key to close this window..."
echo ""
