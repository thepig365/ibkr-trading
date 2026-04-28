"""Trader-facing dashboard primary area (no IBKR on GET)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_dashboard_returns_200(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200


def test_dashboard_trading_day_summary_and_cockpit_en(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Trading Day Summary" in r.text
    assert "Trader Cockpit" in r.text
    assert "Quick links" in r.text
    assert "Latest trades" in r.text
    assert "<details" in r.text


def test_dashboard_zh_lang_shows_summary_or_cockpit(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "交易日摘要" in r.text or "交易员驾驶舱" in r.text
    assert "快捷入口" in r.text


def test_dashboard_diagnostics_inside_collapsed_primary_first(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "<details" in r.text
    tc = r.text
    assert tc.index("Trading Day Summary") < tc.index("Operational snapshot")


def test_dashboard_explains_when_open_but_no_closed(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    p = pod / "2026-x-intraday-paper-orders.jsonl"
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
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "no closed trades with exit data" in r.text


def test_dashboard_complete_trade_charts_buttons(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "complete-trade-charts" in r.text


def test_dashboard_developer_fold_title(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Developer / Engine Diagnostics" in r.text


def test_dashboard_automatic_engine_only_under_diagnostics_fold(tmp_path: Path) -> None:
    text = _client(tmp_path).get("/dashboard").text
    pivot = text.find("Trading Day Summary")
    fold = text.find("Developer / Engine Diagnostics")
    assert pivot >= 0 and fold > pivot
    between = text[pivot:fold]
    assert "run-automatic-paper-engine" not in between
    assert "complete-trade-charts" in between


def test_dashboard_get_does_not_need_ibkr(tmp_path: Path) -> None:
    """GET /dashboard must not require TWS; empty project has no broker hooks in template."""

    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
