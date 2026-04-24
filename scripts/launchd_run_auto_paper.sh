#!/usr/bin/env bash
# Entry used by launchd; runs the loop in background job context. PAPER only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
# shellcheck disable=SC1091
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
else
  echo "Missing .venv" >&2
  exit 1
fi
export PYTHONUNBUFFERED=1
exec python -m bot.cli run-auto-paper-mtf-loop \
  --source dynamic \
  --interval-minutes 5 \
  --market-hours-only \
  --telegram \
  --limit 20
