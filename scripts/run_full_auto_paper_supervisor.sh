#!/usr/bin/env bash
# Full-auto paper supervisor — PAPER ONLY. Requires TWS/IB Gateway (paper) + API port.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 -m bot.cli run-full-auto-paper-supervisor --session full --telegram --report-on-exit "$@"
