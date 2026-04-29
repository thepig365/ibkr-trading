#!/bin/bash
# Forex auto paper — one supervisor iteration (YAML + runtime gates).
# Repo manual use. launchd installs a copy via install_forex_auto_paper_launchd.sh.
# PAPER ONLY. No market orders.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs data/runtime

LOG="${HOME}/Library/Logs/StrategyLab/forex_auto_paper_supervisor.log"
mkdir -p "$(dirname "${LOG}")"

LOCKFILE="${REPO_DIR}/data/runtime/forex_auto_paper_supervisor.lock"

exec 200>"${LOCKFILE}"
if ! flock -n 200; then
  echo "==== $(date -u) skip: another forex auto paper instance holds the lock ====" >> "${LOG}"
  exit 0
fi

exec >> "${LOG}" 2>&1
echo "==== $(date -u) forex auto paper single pass ===="

exec python3 -m bot.cli run-forex-auto-paper-supervisor --json
