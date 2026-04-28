"""Trade chart annotation metadata (no matplotlib required)."""

from __future__ import annotations

from bot.trade_journal_chart import trade_chart_annotation_meta


def test_chart_meta_entry_stop_target() -> None:
    obj = {
        "entry": 10.0,
        "stop": 9.5,
        "target": 11.0,
        "skipped_reasons": [],
    }
    m = trade_chart_annotation_meta(obj)
    assert m["show_stop_hline"] and m["show_target_hline"]
    assert m["show_entry_hline"]
    assert not m["show_exit"]


def test_chart_meta_exit_only_when_time_and_price() -> None:
    base = {
        "entry": 10.0,
        "stop": 9.5,
        "target": 11.0,
        "skipped_reasons": [],
    }
    assert not trade_chart_annotation_meta({**base, "exit_price": 10.2})["show_exit"]
    assert not trade_chart_annotation_meta(
        {**base, "exit_time": "2026-04-24T16:00:00Z"}
    )["show_exit"]
    assert trade_chart_annotation_meta(
        {
            **base,
            "exit_time": "2026-04-24T16:00:00Z",
            "exit_price": 10.2,
        }
    )["show_exit"]


def test_chart_meta_skipped_potential_entry() -> None:
    m = trade_chart_annotation_meta(
        {
            "entry": 10.0,
            "skipped_reasons": ["bracket incomplete"],
            "stop": 9.0,
            "target": 11.0,
        }
    )
    assert m["entry_is_potential_only"]
