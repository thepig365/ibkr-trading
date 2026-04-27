#!/bin/bash
# Full-auto paper supervisor — PAPER ONLY. For manual/Terminal use from the repo.
# launchd: use `install_full_auto_paper_launchd.sh` — it installs a wrapper under
# ~/Library/Application Support/StrategyLab/ so macOS does not block execution under ~/Documents.
# Logs append here; single-instance lock. No secrets printed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs data/runtime

LOG="${REPO_DIR}/logs/full_auto_paper_supervisor.log"
LOCKFILE="${REPO_DIR}/data/runtime/full_auto_paper_supervisor.lock"

exec 200>"${LOCKFILE}"
if ! flock -n 200; then
  echo "==== $(date -u) skip: another full-auto supervisor instance holds the lock (paper only) ====" >> "${LOG}"
  exit 0
fi

exec >> "${LOG}" 2>&1
echo "==== $(date -u) starting full auto paper supervisor (PAPER, LIMIT brackets) ===="

# Safer than raw engine: outer gates + NY window + Telegram blockers, then run-automatic-paper-engine
exec python3 -m bot.cli run-full-auto-paper-supervisor --session full --telegram --report-on-exit
