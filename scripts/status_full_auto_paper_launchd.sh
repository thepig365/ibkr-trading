#!/bin/bash
# Read-only status: launchd, logs, TWS, readiness. Does not trade.
set -euo pipefail

REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
cd "${REPO_ROOT}" || true
LABEL="com.strategy-lab.full-auto-paper"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
SUPPORT_DIR="${HOME}/Library/Application Support/StrategyLab"
ERR_LOG="${LOG_DIR}/launchd_full_auto.err.log"
OUT_LOG="${LOG_DIR}/launchd_full_auto.out.log"
WRAP_LOG="${LOG_DIR}/full_auto_paper_supervisor.log"

echo "=== launchd (${LABEL}) ==="
if [[ -f "${PLIST}" ]]; then
  echo "plist installed: yes (${PLIST})"
else
  echo "plist installed: no"
fi
launchctl list 2>/dev/null | grep -F "strategy-lab" || echo "(no matching launchctl row — job may be idle or not loaded)"

if [[ -f "${ERR_LOG}" ]] && grep -q "Operation not permitted" "${ERR_LOG}" 2>/dev/null; then
  echo ""
  echo "DIAGNOSIS: 'Operation not permitted' appears in ${ERR_LOG}"
  echo "  macOS privacy (TCC) often blocks background launchd jobs from using files under"
  echo "  ~/Documents, Desktop, or iCloud-backed folders."
  echo ""
  echo "  Fixes (pick one):"
  echo "  1) Move the repo to a non-protected path, e.g.:"
  echo "       ${HOME}/StrategyLab/ibkr-trading-bot"
  echo "     then: export STRATEGY_LAB_REPO_DIR=\"${HOME}/StrategyLab/ibkr-trading-bot\""
  echo "     then: bash ${REPO_ROOT}/scripts/install_full_auto_paper_launchd.sh"
  echo "  2) System Settings > Privacy & Security > Full Disk Access:"
  echo "     add /bin/bash and the Python you use (e.g. /usr/bin/python3 or your .venv), then retry."
  echo ""
fi

if [[ -f "${ERR_LOG}" ]] && grep -q "getcwd: cannot access parent directories" "${ERR_LOG}" 2>/dev/null; then
  echo "DIAGNOSIS: getcwd error in launchd logs — often fixed by reinstalling with the"
  echo "  updated install script (WorkingDirectory is no longer the repo under Documents)."
  echo ""
fi

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

echo ""
echo "=== launchd + wrapper logs (Library/Logs) — last 30 lines each ==="
for L in "${OUT_LOG}" "${ERR_LOG}" "${WRAP_LOG}"; do
  echo "--- ${L} ---"
  if [[ -f "${L}" ]]; then
    tail -n 30 "${L}"
  else
    echo "(file missing)"
  fi
  echo ""
done

LOG1="${REPO_ROOT}/logs/full_auto_paper_supervisor.log"
LOG2="${REPO_ROOT}/logs/launchd_full_auto.out.log"
LOG3="${REPO_ROOT}/logs/launchd_full_auto.err.log"
for L in "${LOG1}" "${LOG2}" "${LOG3}"; do
  echo "=== last 30 lines (under repo, may be empty if only Library logs used): ${L} ==="
  if [[ -f "${L}" ]]; then
    tail -n 30 "${L}"
  else
    echo "(file missing)"
  fi
  echo ""
done

echo "wrapper installed: $([[ -f "${SUPPORT_DIR}/run_full_auto_paper_supervisor.sh" ]] && echo yes || echo no) (${SUPPORT_DIR})"
echo ""
echo "=== full-auto-paper-readiness (read-only) ==="
cd "${REPO_ROOT}"
python3 -m bot.cli full-auto-paper-readiness --json 2>/dev/null || true
