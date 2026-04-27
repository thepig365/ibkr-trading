#!/bin/bash
# Remove launchd job; does not delete log files, reports, or runtime JSON under the repo.
set -euo pipefail

LABEL="com.strategy-lab.full-auto-paper"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "${DEST}" ]]; then
  launchctl unload "${DEST}" 2>/dev/null || true
  rm -f "${DEST}"
  echo "Removed ${DEST}"
else
  echo "No plist at ${DEST} (nothing to remove)"
fi

WRAP="${HOME}/Library/Application Support/StrategyLab/run_full_auto_paper_supervisor.sh"
if [[ -f "${WRAP}" ]]; then
  rm -f "${WRAP}"
  echo "Removed wrapper ${WRAP}"
fi
