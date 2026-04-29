#!/bin/bash
# Read-only: launchd + logs for Forex auto paper.
set -euo pipefail

REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
cd "${REPO_ROOT}" || true

LABEL="com.strategy-lab.forex-auto-paper"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
SUPPORT_DIR="${HOME}/Library/Application Support/StrategyLab"
ERR_LOG="${LOG_DIR}/launchd_forex_auto_paper.err.log"
OUT_LOG="${LOG_DIR}/launchd_forex_auto_paper.out.log"
WRAP_LOG="${LOG_DIR}/forex_auto_paper_supervisor.log"

echo "=== launchd (${LABEL}) ==="
if [[ -f "${PLIST}" ]]; then
  echo "plist installed: yes (${PLIST})"
else
  echo "plist installed: no"
fi
launchctl list 2>/dev/null | grep -F "strategy-lab" || echo "(no matching launchctl row)"

echo ""
echo "wrapper: $([[ -f "${SUPPORT_DIR}/forex_auto_paper_launchd_wrapper.sh" ]] && echo yes || echo no)"

echo ""
echo "=== last 25 lines — launchd forex logs ==="
for L in "${OUT_LOG}" "${ERR_LOG}" "${WRAP_LOG}"; do
  echo "--- ${L} ---"
  [[ -f "${L}" ]] && tail -n 25 "${L}" || echo "(missing)"
  echo ""
done

echo ""
echo "=== forex-auto-paper-readiness (JSON, read-only) ==="
cd "${REPO_ROOT}"
python3 -m bot.cli forex-auto-paper-readiness --json 2>/dev/null || true
