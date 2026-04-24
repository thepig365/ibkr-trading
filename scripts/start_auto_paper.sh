#!/usr/bin/env bash
# Start the already-installed LaunchAgent (loads job if user skipped enable).
set -euo pipefail
LABEL="com.leon.ibkr-trading-bot.auto-paper"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
mkdir -p "$ROOT/logs"
if [[ ! -f "$PLIST" ]]; then
  echo "Install first: bash $ROOT/scripts/install_launch_agent.sh" >&2
  exit 1
fi
# macOS: prefer kickstart; fallback to start
if launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null; then
  echo "Started (kickstart) $LABEL"
elif launchctl start "$LABEL" 2>/dev/null; then
  echo "Started $LABEL"
else
  # Ensure loaded
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null || true
  launchctl kickstart "gui/$(id -u)/${LABEL}" 2>/dev/null || launchctl start "$LABEL" || {
    echo "Could not start $LABEL. Try: launchctl list | grep $LABEL" >&2
    exit 1
  }
  echo "Started $LABEL (after bootstrap)"
fi
