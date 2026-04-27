#!/bin/bash
set -euo pipefail
REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
PLIST="${HOME}/Library/LaunchAgents/com.strategy-lab.telegram-listener.plist"
WRAPPER="${HOME}/Library/Application Support/StrategyLab/run_telegram_command_listener.sh"

if [[ -f "${PLIST}" ]]; then
  launchctl unload "${PLIST}" 2>/dev/null || true
  rm -f "${PLIST}"
  echo "Removed ${PLIST}"
else
  echo "No launchd plist at ${PLIST}"
fi
if [[ -f "${WRAPPER}" ]]; then
  rm -f "${WRAPPER}"
  echo "Removed ${WRAPPER}"
fi
echo "Done (logs under ~/Library/Logs/StrategyLab/ not deleted)."
