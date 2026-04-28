"""Trade list: local vs broker state columns."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=25,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_trades_broker_column_not_checked_en(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    row = {
        "symbol": "SPY",
        "timestamp": "2026-04-01T15:30:00",
        "submitted": True,
        "direction": "long",
        "strategy_id": "t",
        "signal_category": "x",
        "entry": 100.0,
        "stop": 99.0,
        "skipped_reasons": [],
    }
    (pod / "2026-04-01-intraday-paper-orders.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    txt = _client(tmp_path).get("/trades").text
    assert "Not checked" in txt
    assert "Local state" in txt
    assert "Broker state" in txt


def test_trades_get_does_not_call_ib_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []

    def _spy_connect(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)

    monkeypatch.setattr(
        "bot.ibkr_connection.connect_readonly_roster_retry",
        _spy_connect,
    )
    txt = _client(tmp_path).get("/trades").text
    assert calls == [], "GET /trades must not initiate IBKR sockets"
    assert txt.count("broker-snapshot-refresh") == 0
