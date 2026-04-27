#!/bin/bash
# Foreground: Telegram getUpdates command listener (read-only commands).
# Repo root = parent of scripts/
set -euo pipefail
REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
export IBKR_TRADING_PROJECT_ROOT="${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}"
cd "${REPO_ROOT}"
if [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
  exec "${REPO_ROOT}/.venv/bin/python3" -m bot.cli telegram-command-listener "$@"
else
  exec /usr/bin/python3 -m bot.cli telegram-command-listener "$@"
fi
