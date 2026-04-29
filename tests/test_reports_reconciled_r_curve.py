"""USD equity gate uses reconciliation USD when attached to closed trades."""

from __future__ import annotations

import pytest

from bot.trade_ledger import TradeLedgerRecord
from bot.trade_reports import (
    all_closed_trades_have_reliable_usd,
    realized_pnl_usd_from_trade_record,
)


def _closed_trade() -> TradeLedgerRecord:
    rec = TradeLedgerRecord(
        trade_id="z" * 22,
        symbol="QQQ",
        direction="long",
        strategy="s",
        mode_signal="m",
        status_slug="closed",
        submitted_time="2026-01-01T00:00:00Z",
        entry_time=None,
        entry_price=None,
        exit_time="2026-01-01T04:00:00Z",
        exit_price=1.5,
        stop_price=None,
        target_price=None,
        qty=1,
        notional=None,
        planned_rr=None,
        realized_r=1,
        close_reason="target_hit",
        submitted_to_broker=False,
        skipped_reason_raw="",
        bracket_status="",
        parent_entry_order_id=None,
        stop_order_id=None,
        target_order_id=None,
        raw_json={},
        ict_labels="",
        fill_reconciliation={
            "realized_pnl_usd": 10.5,
            "status": "closed",
        },
    )
    return rec


def test_realized_pnl_prefers_fill_reconciliation() -> None:
    raw = _closed_trade()
    assert realized_pnl_usd_from_trade_record(raw) == pytest.approx(10.5)


def test_all_closed_reliable_with_reconcile_usd_only() -> None:
    rows = [_closed_trade()]
    assert all_closed_trades_have_reliable_usd(rows) is True
