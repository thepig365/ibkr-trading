#!/usr/bin/env bash
# Read-only environment checks. Optional flags: --pytest, --check-ibkr. No orders; no broker RPC.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
bash "$ROOT/scripts/strategy_lab_doctor.sh" "$@"
echo ""
echo "Press Enter to close this window."
read -r
