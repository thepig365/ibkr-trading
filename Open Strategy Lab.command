#!/usr/bin/env bash
# Open the Strategy Lab home page in the default browser (no IBKR connection; no orders).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
bash "$ROOT/scripts/open_strategy_lab_ui.sh" || true
echo ""
echo "Press Enter to close this window."
read -r
