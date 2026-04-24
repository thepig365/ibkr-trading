#!/usr/bin/env bash
# Unloads and removes the LaunchAgent plist.
set -euo pipefail
LABEL="com.leon.ibkr-trading-bot.auto-paper"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
if [[ -f "$DEST" ]]; then
  launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl unload "$DEST" 2>/dev/null || true
  rm -f "$DEST"
  echo "Removed $DEST"
else
  echo "No plist at $DEST"
fi
