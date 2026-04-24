#!/usr/bin/env bash
# Stop the LaunchAgent job (does not remove plist).
set -euo pipefail
LABEL="com.leon.ibkr-trading-bot.auto-paper"
if launchctl list "$LABEL" &>/dev/null; then
  launchctl stop "$LABEL" || true
  echo "Stopped $LABEL (if it was running)."
else
  echo "Label $LABEL not loaded in launchctl; nothing to stop."
fi
