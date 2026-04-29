#!/bin/bash
# Installed copy: ~/Library/Application Support/StrategyLab/forex_auto_paper_launchd_wrapper.sh
# launchd invokes this every 60s — one CLI iteration (gates inside Python).
set -euo pipefail

REPO="${STRATEGY_LAB_REPO_DIR:?STRATEGY_LAB_REPO_DIR must be set}"
export IBKR_TRADING_PROJECT_ROOT="$REPO"
export PYTHONPATH="$REPO"
export IBKR_ACCOUNT_MODE="${IBKR_ACCOUNT_MODE:-paper}"

SUPPORT="${HOME}/Library/Application Support/StrategyLab"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
mkdir -p "$LOG_DIR" "$SUPPORT"

LOG="${LOG_DIR}/forex_auto_paper_supervisor.log"
LOCK_DIR="${SUPPORT}/forex_auto_paper_supervisor.lock.run"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "==== $(date -u) skip: forex auto paper lock busy ====" >> "$LOG"
  exit 0
fi
_cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap _cleanup_lock EXIT INT TERM HUP

exec >> "$LOG" 2>&1
echo "==== $(date -u) launchd forex auto paper project=${REPO} ===="

if [[ -n "${STRATEGY_LAB_PYTHON:-}" ]]; then
  PYTHON_BIN="${STRATEGY_LAB_PYTHON}"
elif [[ -x "${REPO}/.venv/bin/python3" ]]; then
  PYTHON_BIN="${REPO}/.venv/bin/python3"
else
  PYTHON_BIN="/usr/bin/python3"
fi

set +e
"$PYTHON_BIN" -m bot.cli run-forex-auto-paper-supervisor --json
exit $?
