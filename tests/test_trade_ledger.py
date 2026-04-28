"""Normalized trade ledger from local paper_orders JSONL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bot.journal_trade_id import compute_stable_trade_row_id
from bot.trade_ledger import build_trade_records, find_trade_record


def test_build_trade_records_sorted_and_fields(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "2026-04-24-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-24T15:00:00Z",
        "symbol": "X",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "submitted_to_broker": True,
        "skipped_reasons": [],
        "entry": 10.0,
        "stop": 9.5,
        "target": 11.0,
        "planned_rr": 2.0,
        "bracket_integrity": "complete",
        "exit_time": "2026-04-24T16:00:00Z",
        "exit_price": 10.5,
    }
    lp.write_text(json.dumps(row) + "\n", encoding="utf-8")
    recs = build_trade_records(tmp_path)
    assert len(recs) == 1
    r = recs[0]
    assert r.symbol == "X"
    assert r.status_slug == "closed"
    assert r.submitted_time.startswith("2026-04-24")
    assert r.entry_price == 10.0
    assert r.exit_price == 10.5
    assert r.exit_time


def test_skipped_row_has_human_reason(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "skip-intraday-paper-orders.jsonl"
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": "Z",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": False,
        "skipped_reasons": ["bracket incomplete"],
        "entry": 5.0,
        "stop": 4.9,
        "target": 5.2,
    }
    lp.write_text(json.dumps(row) + "\n", encoding="utf-8")
    r = build_trade_records(tmp_path)[0]
    assert r.status_slug == "skipped"
    assert r.skipped_reason_human or r.skipped_reason_raw


def test_find_trade_record_returns_hydrated(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "find-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-24T14:00:00Z",
        "symbol": "AB",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": False,
        "skipped_reasons": ["x"],
        "entry": 1.0,
        "stop": 0.9,
        "target": 1.1,
    }
    lp.write_text(json.dumps(row) + "\n", encoding="utf-8")
    abs_p = str(lp.resolve())
    obj = json.loads(lp.read_text(encoding="utf-8").strip().split("\n")[0])
    tid = compute_stable_trade_row_id(abs_p, 0, obj).lower()
    got = find_trade_record(tmp_path, tid)
    assert got is not None
    assert got.trade_id == tid
    assert got.chart_status
