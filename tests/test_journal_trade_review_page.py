"""Trade review routes and journal table ergonomics."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def client_and_project(tmp_path: Path) -> tuple[TestClient, Path]:
    (tmp_path / "data").mkdir()
    state = LocalFileStateStore(tmp_path)
    queue = LocalCommandRunner(
        project_root=tmp_path,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=tmp_path / "ui_audit.jsonl",
    )
    app = create_app(project_root=tmp_path, state_store=state, command_queue=queue)
    return TestClient(app), tmp_path


def _write_journal_row(proj: Path) -> str:
    pod = proj / "data" / "paper_orders"
    pod.mkdir(parents=True)
    out = pod / "day-intraday-paper-orders.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "REVU",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 100.0,
        "stop": 99.0,
        "target": 103.0,
        "planned_rr": 3.0,
        "quantity": 3,
        "estimated_notional": 300.0,
        "order_ids": [1, 2, 3],
        "paper_only": True,
        "live_trading_allowed": False,
        "bracket_integrity": "complete",
        "tif": "DAY",
        "sizing_audit": {"final_quantity": 3, "risk_based_quantity": 3.1},
        "chart_paths": [],
    }
    out.write_text(json.dumps(row) + "\n", encoding="utf-8")
    st = LocalFileStateStore(proj)
    v = st.get_journal_view(limit=5)
    assert len(v.paper_orders) >= 1
    return v.paper_orders[0].trade_id


def test_journal_main_table_columns_and_details_hidden(client_and_project):
    cli, proj = client_and_project
    tid = _write_journal_row(proj)
    r = cli.get("/journal")
    assert r.status_code == 200
    html = r.text
    assert "Show sizing details" in html
    assert "final_quantity" in html  # from sizing_audit_json in expanded pre
    assert "View Trade" in html
    assert tid in html


def test_trade_review_returns_200(client_and_project) -> None:
    cli, proj = client_and_project
    tid = _write_journal_row(proj)
    rr = cli.get(f"/journal/trade/{tid}")
    assert rr.status_code == 200
    body = rr.text
    assert "REVU" in body
    assert "100." in body
    assert tid in body


def test_trade_review_zh_lang(client_and_project) -> None:
    cli, proj = client_and_project
    tid = _write_journal_row(proj)
    rr = cli.get(f"/journal/trade/{tid}?lang=zh")
    assert rr.status_code == 200
    zh = rr.text
    assert "交易复盘" in zh
    assert "入场" in zh


def _write_skipped_journal_row(proj: Path) -> str:
    pod = proj / "data" / "paper_orders"
    pod.mkdir(parents=True)
    out = pod / "skipped-intraday-paper-orders.jsonl"
    raw_skip = "open order exists for PLTR — refuse duplicate paper entry"
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "SKIP",
        "direction": "short",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": False,
        "skipped_reasons": [raw_skip],
        "entry": 50.0,
        "stop": 51.0,
        "target": 48.0,
        "planned_rr": 2.0,
        "quantity": 1,
        "estimated_notional": 50.0,
        "order_ids": [],
        "paper_only": True,
        "live_trading_allowed": False,
        "bracket_integrity": "incomplete",
        "tif": "DAY",
        "chart_paths": [],
    }
    out.write_text(json.dumps(row) + "\n", encoding="utf-8")
    st = LocalFileStateStore(proj)
    v = st.get_journal_view(limit=50)
    match = [r for r in v.paper_orders if r.symbol == "SKIP"]
    assert match, "expected skipped row"
    return match[0].trade_id


def test_skipped_trade_human_reason_and_raw_reason_still_present(
    client_and_project,
) -> None:
    cli, proj = client_and_project
    tid = _write_skipped_journal_row(proj)
    rr = cli.get(f"/journal/trade/{tid}")
    assert rr.status_code == 200
    body = rr.text
    assert "open order" in body.lower() or "duplicate" in body.lower()
    assert "open order exists for PLTR" in body


def test_sent_trade_shows_prices_rr(client_and_project) -> None:
    cli, proj = client_and_project
    tid = _write_journal_row(proj)
    rr = cli.get(f"/journal/trade/{tid}")
    assert "3.00" in rr.text
    assert "300" in rr.text  # notional
