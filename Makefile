# Strategy Lab — local dev helpers (paper-only, no order placement in these targets)
.PHONY: strategy-lab strategy-lab-bg strategy-lab-stop strategy-lab-status strategy-lab-smoke

# Foreground: open http://127.0.0.1:8765
strategy-lab:
	python3 -m bot_ui

# Background: PID in data/runtime/strategy_lab_ui.pid, logs in logs/
strategy-lab-bg:
	./scripts/start_strategy_lab_ui.sh

strategy-lab-stop:
	./scripts/stop_strategy_lab_ui.sh

strategy-lab-status:
	./scripts/status_strategy_lab_ui.sh

strategy-lab-open:
	./scripts/open_strategy_lab_ui.sh

strategy-lab-doctor:
	./scripts/strategy_lab_doctor.sh

# Pages + healthz + engine-status CLI (no TWS, no browser)
strategy-lab-smoke:
	python3 -m pytest -q tests/test_engine_launch_workflow.py
