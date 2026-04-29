"""Unit tests for fills reconciliation matching (mocked fills)."""

from __future__ import annotations

import json
from pathlib import Path

from bot.fills_reconciliation import (
    TradeReconStatus,
    match_fills_to_local_trades,
    compute_realized_metrics,
)
from bot.trade_ledger import raw_dict_to_trade_record


def _rec(abs_p: str, line: int, row: dict) -> object:
    return raw_dict_to_trade_record(abs_p, line, row)


def test_short_realized_rr() -> None:
    rr, usd = compute_realized_metrics("short", entry_p=100.0, exit_p=98.0, qty_abs=10.0, stop_plan=103.0)
    assert rr is not None and rr > 0
    assert usd is not None and usd > 0


def test_long_rr() -> None:
    rr, _ = compute_realized_metrics("long", entry_p=10.0, exit_p=11.0, qty_abs=1.0, stop_plan=9.5)
    assert rr == 2.0


def test_closed_target_hit_via_order_ids(tmp_path: Path) -> None:
    abs_p = str(tmp_path / "x.jsonl")
    row = {
        "timestamp": "2026-04-28T14:30:00Z",
        "symbol": "SPY",
        "strategy_id": "t",
        "direction": "long",
        "signal_category": "x",
        "submitted": True,
        "submitted_to_broker": True,
        "skipped_reasons": [],
        "quantity": 1.0,
        "entry": 500.0,
        "stop": 490.0,
        "target": 510.0,
        "planned_rr": 1.0,
        "parent_entry_order_id": 111,
        "parent_sl_order_id": 222,
        "parent_tp_order_id": 333,
    }
    rec = _rec(abs_p, 0, row)
    fills_by_order = {
        111: [
            {
                "exec_id": "e1",
                "order_id": 111,
                "symbol": "SPY",
                "side": "BOT",
                "quantity": 1.0,
                "price": 500.0,
                "time": "2026-04-28T14:35:00+00:00",
            },
        ],
        333: [
            {
                "exec_id": "e2",
                "order_id": 333,
                "symbol": "SPY",
                "side": "SLD",
                "quantity": 1.0,
                "price": 510.0,
                "time": "2026-04-28T15:01:00+00:00",
            },
        ],
    }
    out = match_fills_to_local_trades([rec], fills_by_order, positions_by_symbol={}, open_orders_by_id={})
    assert len(out) == 1
    t = out[0]
    assert t.status == TradeReconStatus.closed


def test_submitted_no_fills(tmp_path: Path) -> None:
    abs_p = str(tmp_path / "y.jsonl")
    row = {
        "timestamp": "2026-04-28T14:30:00Z",
        "symbol": "QQQ",
        "strategy_id": "t",
        "direction": "long",
        "submitted": True,
        "submitted_to_broker": True,
        "skipped_reasons": [],
        "parent_entry_order_id": 999991,
        "quantity": 1,
    }
    rec = _rec(abs_p, 0, row)
    out = match_fills_to_local_trades([rec], {}, positions_by_symbol={}, open_orders_by_id={})
    assert out[0].status == TradeReconStatus.submitted_not_filled
