#!/bin/bash
# Installed copy lives at ~/Library/Application Support/StrategyLab/
# (launchd cannot rely on scripts under ~/Documents — TCC / Full Disk Access).
# PAPER ONLY. No live trading. Env STRATEGY_LAB_REPO_DIR set by launchd plist.
set -euo pipefail

REPO="${STRATEGY_LAB_REPO_DIR:?STRATEGY_LAB_REPO_DIR must be set}"
export IBKR_TRADING_PROJECT_ROOT="$REPO"
export PYTHONPATH="$REPO"
export IBKR_ACCOUNT_MODE="${IBKR_ACCOUNT_MODE:-paper}"

SUPPORT="${HOME}/Library/Application Support/StrategyLab"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
mkdir -p "$LOG_DIR" "$SUPPORT"

# Logs under Library (not under repo) so launchd can write if Documents is protected
LOG="${LOG_DIR}/full_auto_paper_supervisor.log"
# Portable lock: macOS launchd often has no `flock` in PATH; mkdir is atomic.
LOCK_DIR="${SUPPORT}/full_auto_paper_supervisor.lock.run"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "==== $(date -u) skip: another instance holds lock (paper only) ====" >> "$LOG"
  exit 0
fi
_cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap _cleanup_lock EXIT INT TERM HUP

exec >> "$LOG" 2>&1
echo "==== $(date -u) launchd wrapper start project=${REPO} ===="

# Prefer explicit python (e.g. Homebrew) if venv under Documents is not executable from launchd
if [[ -n "${STRATEGY_LAB_PYTHON:-}" ]]; then
  PYTHON_BIN="${STRATEGY_LAB_PYTHON}"
elif [[ -x "${REPO}/.venv/bin/python3" ]]; then
  PYTHON_BIN="${REPO}/.venv/bin/python3"
else
  PYTHON_BIN="/usr/bin/python3"
fi

set +e
"$PYTHON_BIN" -m bot.cli run-full-auto-paper-supervisor --session full --telegram --report-on-exit
exit $?
