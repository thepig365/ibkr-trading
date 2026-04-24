#!/usr/bin/env bash
# Installs the LaunchAgent plist to ~/Library/LaunchAgents/ (no secrets in plist).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/launchd/com.leon.ibkr-trading-bot.auto-paper.plist"
LABEL="com.leon.ibkr-trading-bot.auto-paper"
DEST_DIR="$HOME/Library/LaunchAgents"
DEST="$DEST_DIR/${LABEL}.plist"
mkdir -p "$ROOT/logs" "$DEST_DIR"
if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi
TMP="$(mktemp)"
sed "s|__PROJECT_ROOT__|${ROOT}|g" "$SRC" > "$TMP"
# Basic guard: no token-like secrets should appear
if grep -Ei 'telegram|token|api_?key|password|secret' "$TMP" >& /dev/null; then
  echo "Refusing: generated plist may contain disallowed substrings" >&2
  exit 1
fi
# Unload if already loaded
if [[ -f "$DEST" ]]; then
  launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl unload "$DEST" 2>/dev/null || true
fi
cp "$TMP" "$DEST"
rm -f "$TMP"
chmod 644 "$DEST"
# Load for current user session
launchctl bootstrap "gui/$(id -u)" "$DEST" 2>/dev/null || launchctl load -w "$DEST" 2>/dev/null || {
  echo "Note: if load failed, try: launchctl load -w $DEST" >&2
}
echo "Installed: $DEST"
echo "Start: bash $ROOT/scripts/start_auto_paper.sh"
echo "Stop:  bash $ROOT/scripts/stop_auto_paper.sh"
