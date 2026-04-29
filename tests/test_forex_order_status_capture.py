"""Forex orders JSONL summarize for UI."""

from __future__ import annotations

from bot.forex.orders_ui import summarize_row_for_ui


def test_summarize_capture_reject_when_broker_failed() -> None:
    rec = {
        "ts_utc": "2026-01-01",
        "pair": "AUD/USD",
        "broker": {"ok": False, "error": "qualify_failed"},
        "direction": "long",
        "units": 1000,
        "phase": "auto_paper",
    }
    s = summarize_row_for_ui(rec)
    assert s["broker_acceptance"] is False
    assert "qualify_failed" in (s["reject_reason"] or "")


def test_summarize_accepts_nested_status_snapshots() -> None:
    rec = {
        "pair": "USD/JPY",
        "broker": {"ok": True, "statuses": [{"status": "Submitted", "filled": 0.0}]},
    }
    s = summarize_row_for_ui(rec)
    assert s["broker_acceptance"] is True
