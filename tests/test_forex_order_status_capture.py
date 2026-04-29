"""Forex audit JSONL (order status snapshots)."""

from __future__ import annotations

from bot.forex.orders_log import append_forex_order_event, forex_orders_path


def test_orders_jsonl_written(tmp_path) -> None:
    append_forex_order_event(
        tmp_path,
        {
            "status": "submitted",
            "api_submit_attempted": True,
            "message": "",
        },
    )
    p = forex_orders_path(tmp_path)
    assert p.exists()
    assert '"status"' in p.read_text(encoding="utf-8")
