#!/bin/bash
# Read-only: plist file, launchctl row, last log lines. No Telegram API.
set -euo pipefail
REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
PLIST="${HOME}/Library/LaunchAgents/com.strategy-lab.telegram-listener.plist"
WRAPPER="${HOME}/Library/Application Support/StrategyLab/run_telegram_command_listener.sh"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
STATE="${REPO_ROOT}/data/runtime/telegram_command_listener_state.json"

echo "=== com.strategy-lab.telegram-listener ==="
if [[ -f "${PLIST}" ]]; then
  echo "plist: yes  ${PLIST}"
else
  echo "plist: no"
fi
if [[ -f "${WRAPPER}" ]]; then
  echo "wrapper: yes  ${WRAPPER}"
else
  echo "wrapper: no"
fi
echo ""
if [[ -f "${STATE}" ]]; then
  echo "state file: ${STATE}"
  head -n 8 "${STATE}" 2>/dev/null || true
else
  echo "state file: (missing) ${STATE}"
fi
echo ""
echo "--- launchctl (grep label) ---"
/bin/launchctl list 2>/dev/null | grep -F "com.strategy-lab.telegram-listener" || echo "(not listed or launchctl error)"
echo ""
echo "--- last 20 lines: telegram_command_listener.log ---"
if [[ -f "${LOG_DIR}/telegram_command_listener.log" ]]; then
  tail -n 20 "${LOG_DIR}/telegram_command_listener.log"
else
  echo "(file missing)"
fi
echo ""
echo "--- last 5 lines: telegram_listener.err.log ---"
if [[ -f "${LOG_DIR}/telegram_listener.err.log" ]]; then
  tail -n 5 "${LOG_DIR}/telegram_listener.err.log"
else
  echo "(file missing)"
fi
