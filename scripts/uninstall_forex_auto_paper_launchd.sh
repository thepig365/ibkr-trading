#!/bin/bash
set -euo pipefail

LABEL="com.strategy-lab.forex-auto-paper"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

launchctl unload "${PLIST}" 2>/dev/null || true
rm -f "${PLIST}"

echo "Removed LaunchAgents plist (if present): ${PLIST}"
echo "Optional: rm -f \"${HOME}/Library/Application Support/StrategyLab/forex_auto_paper_launchd_wrapper.sh\""
