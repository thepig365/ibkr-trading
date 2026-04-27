#!/bin/bash
# Read-only status: launchd, logs tail, TWS port, CLI readiness. Does not trade.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"
LABEL="com.strategy-lab.full-auto-paper"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

echo "=== launchd (${LABEL}) ==="
if [[ -f "${PLIST}" ]]; then
  echo "plist installed: yes (${PLIST})"
else
  echo "plist installed: no"
fi
launchctl list 2>/dev/null | grep -F "strategy-lab" || echo "(no matching launchctl row — job may be idle or not loaded)"

echo ""
echo "=== TWS API port (default 7497, localhost) ==="
python3 -c "
import socket
s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    s.connect(('127.0.0.1', 7497))
    print('127.0.0.1:7497 listening: yes')
except OSError:
    print('127.0.0.1:7497 listening: no (open TWS paper + enable API)')
finally:
    s.close()
" 2>/dev/null || echo "could not probe port"

LOG1="${REPO_ROOT}/logs/full_auto_paper_supervisor.log"
LOG2="${REPO_ROOT}/logs/launchd_full_auto.out.log"
LOG3="${REPO_ROOT}/logs/launchd_full_auto.err.log"
for L in "${LOG1}" "${LOG2}" "${LOG3}"; do
  echo ""
  echo "=== last 50 lines: ${L} ==="
  if [[ -f "${L}" ]]; then
    tail -n 50 "${L}"
  else
    echo "(file missing)"
  fi
done

echo ""
echo "=== full-auto-paper-readiness (read-only) ==="
python3 -m bot.cli full-auto-paper-readiness --json 2>/dev/null || true
