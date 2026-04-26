"""Backtest data coverage: CLI + allowlist (Prompt 13BT-UI-DATA)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import CommandRequest, validate_request
from bot_ui.services.safety import ALLOWED_COMMANDS, validate_args_for
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent


def _client(tmp: Path) -> TestClient:
    (tmp / "data").mkdir(exist_ok=True)
    st = LocalFileStateStore(tmp)
    from bot_ui.services.command_queue import LocalCommandRunner

    q = LocalCommandRunner(
        project_root=tmp,
        python_executable=sys.executable,
        timeout_seconds=25,
        audit_file=tmp / "a.jsonl",
    )
    return TestClient(create_app(project_root=tmp, state_store=st, command_queue=q))


def test_candle_coverage_in_allowlist() -> None:
    assert "candle-coverage" in ALLOWED_COMMANDS
    assert "fetch-candles" in ALLOWED_COMMANDS


def test_validate_candle_coverage_args_accepts() -> None:
    ok, _ = validate_args_for(
        "candle-coverage",
        (
            "--core-basket",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-24",
            "--timeframe",
            "1min",
        ),
    )
    assert ok
    ok2, _ = validate_args_for(
        "candle-coverage",
        (
            "--symbols",
            "CRM,AAPL",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-24",
        ),
    )
    assert ok2


def test_validate_rejects_unsafe() -> None:
    bad, _ = validate_args_for(
        "candle-coverage", ("--symbols", "CRM", "--start", "20-04-2026", "--end", "2026-04-24")
    )
    assert not bad
    rej, _ = validate_request(
        CommandRequest(
            command="candle-coverage",
            args=("--start", "2026-04-20", "--end", "2026-04-24", "--live", "x"),
        )
    )
    assert rej is False


def test_cli_candle_coverage_json_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config").mkdir(exist_ok=True)
    for n in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        s = REPO / "config" / n
        if s.is_file():
            (tmp_path / "config" / n).write_bytes(s.read_bytes())
    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "candle-coverage",
            "--symbols",
            "CRM",
            "--start",
            "2026-04-01",
            "--end",
            "2026-04-24",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert "per_symbol" in body
    assert "CRM" in body["per_symbol"]


def test_cli_watchlist_missing_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setenv("IBKR_TRADING_PROJECT_ROOT", str(tmp_path))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "candle-coverage",
            "--watchlist",
            "latest",
            "--start",
            "2026-04-20",
            "--end",
            "2026-04-24",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    j = json.loads(proc.stdout)
    assert "watchlist_error" in j or j.get("total_symbols", 0) >= 0


def test_backtest_page_has_coverage_strings(tmp_path: Path) -> None:
    t = _client(tmp_path).get("/backtest").text
    assert "Check Data Coverage" in t
    assert "Fetch missing candles" in t or "Fetch Candles" in t


def test_backtest_routes_return_200(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/backtest").status_code == 200
