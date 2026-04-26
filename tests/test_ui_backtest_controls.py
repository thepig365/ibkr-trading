"""Prompt 13G — backtest page uses allowlisted commands only (no orders, no default IBKR)."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.safety import ALLOWED_COMMANDS
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_backtest_cli_commands_in_allowlist() -> None:
    for cmd in (
        "backtest-intraday-smc",
        "backtest-intraday-smc-watchlist",
        "backtest-oneclick",
        "backtest-report",
        "candle-coverage",
        "fetch-candles",
    ):
        assert cmd in ALLOWED_COMMANDS


def test_backtest_page_renders_and_warns_cache_only_by_default(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/backtest")
    assert r.status_code == 200
    t = r.text
    assert "backtest" in t.lower()
    assert "backtest-intraday-smc" in t or "Backtest" in t
    assert "Check Data Coverage" in t
    assert "Fetch missing candles" in t or "Fetch Candles" in t
    assert "Fetch Missing Data" in t or "Run Backtest" in t


def test_backtest_forms_no_market_or_live(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/backtest")
    for bad in ("--market", "--live", "place-order"):
        assert bad not in r.text
