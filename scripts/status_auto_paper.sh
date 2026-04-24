#!/usr/bin/env bash
# Show launchctl status, plist presence, and last log lines.
set -euo pipefail
LABEL="com.leon.ibkr-trading-bot.auto-paper"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
echo "== Project: $ROOT"
echo "== Plist: $PLIST"
if [[ -f "$PLIST" ]]; then echo "   installed: yes"; else echo "   installed: no"; fi
echo "== launchctl list $LABEL"
launchctl list "$LABEL" 2>&1 || true
echo "== tail logs (if any)"
for f in "$ROOT/logs/auto-paper.stdout.log" "$ROOT/logs/auto-paper.stderr.log"; do
  echo "--- $f ---"
  if [[ -f "$f" ]]; then tail -n 8 "$f"; else echo "(missing)"; fi
done
if [[ -f "$ROOT/data/runtime/auto_paper_loop_state.json" ]]; then
  echo "== data/runtime/auto_paper_loop_state.json"
  cat "$ROOT/data/runtime/auto_paper_loop_state.json"
fi
