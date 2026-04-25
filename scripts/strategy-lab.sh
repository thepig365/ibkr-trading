#!/usr/bin/env bash
# Back-compat wrapper — prefer scripts/start_strategy_lab_ui.sh etc. (Prompt 13G).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in
  start) exec "$ROOT/scripts/start_strategy_lab_ui.sh" ;;
  stop) exec "$ROOT/scripts/stop_strategy_lab_ui.sh" ;;
  status) exec "$ROOT/scripts/status_strategy_lab_ui.sh" ;;
  open) exec "$ROOT/scripts/open_strategy_lab_ui.sh" ;;
  doctor) exec "$ROOT/scripts/strategy_lab_doctor.sh" "${@:2}" ;;
  restart) "$ROOT/scripts/stop_strategy_lab_ui.sh" && exec "$ROOT/scripts/start_strategy_lab_ui.sh" ;;
  *)
    echo "Usage: $0 {start|stop|status|open|restart|doctor}" >&2
    echo "See docs/strategy-lab-daily-workflow.md" >&2
    exit 1
    ;;
esac
