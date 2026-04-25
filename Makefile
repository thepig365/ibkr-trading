# Strategy Lab — local dev helpers (paper-only, no order placement in these targets)
.PHONY: strategy-lab strategy-lab-bg strategy-lab-stop strategy-lab-status strategy-lab-smoke

# Foreground: open http://127.0.0.1:8765
strategy-lab:
	python3 -m bot_ui

# Background: PID + log under data/runtime/ (see scripts/strategy-lab.sh)
strategy-lab-bg:
	./scripts/strategy-lab.sh start

strategy-lab-stop:
	./scripts/strategy-lab.sh stop

# Engine snapshot (read-only) + optional UI /healthz if process was started via script
strategy-lab-status:
	./scripts/strategy-lab.sh status

# Fast smoke: pages + healthz + engine-status (no TWS, no browser)
strategy-lab-smoke:
	python3 -m pytest -q tests/test_strategy_lab_smoke.py
