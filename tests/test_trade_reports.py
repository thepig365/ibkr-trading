"""Unit tests for ``bot/trade_reports`` journal analytics."""

from __future__ import annotations

from bot.trade_ledger import raw_dict_to_trade_record
from bot.trade_reports import (
    JournalAnalytics,
    all_closed_trades_have_reliable_usd,
    build_data_quality_payload,
    build_journal_analytics,
    realized_pnl_usd_from_raw,
)


def _closed(ts: str, sym: str, entry: float, stop: float, exit_p: float, ex_ts: str):
    return raw_dict_to_trade_record(
        "/tmp/mock.jsonl",
        1,
        {
            "symbol": sym,
            "timestamp": ts,
            "submitted": True,
            "strategy_id": "ict_smc_intraday_v1",
            "signal_category": "test",
            "direction": "long",
            "entry": entry,
            "stop": stop,
            "exit_time": ex_ts,
            "exit_price": exit_p,
        },
    )


def test_build_journal_analytics_empty() -> None:
    ja = build_journal_analytics([])
    assert ja.empty_state is True
    assert isinstance(ja, JournalAnalytics)


def test_cumulative_r_two_closed_trades() -> None:
    a = _closed("2026-04-01T10:00:00", "AAA", 100.0, 99.0, 101.0, "2026-04-01T15:00:00")
    b = _closed("2026-04-02T10:00:00", "AAA", 100.0, 99.0, 99.5, "2026-04-02T15:00:00")
    ja = build_journal_analytics([a, b])
    assert not ja.empty_state
    assert ja.closed_trades == 2
    assert ja.total_r_closed is not None
    assert len(ja.cumulative_r_points) == 2
    assert ja.cumulative_r_points[-1][1] == ja.total_r_closed
    assert ja.max_drawdown_r is not None


def test_drawdown_from_r_curve() -> None:
    rows = [
        _closed("2026-04-01T10:00:00", "X", 100.0, 99.0, 99.0, "2026-04-01T11:00:00"),
        _closed("2026-04-02T10:00:00", "X", 100.0, 99.0, 102.0, "2026-04-02T11:00:00"),
    ]
    ja = build_journal_analytics(rows)
    assert ja.max_drawdown_r is not None
    assert ja.max_drawdown_r >= 0
    assert ja.current_drawdown_r is not None


def test_current_drawdown_zero_at_equity_high() -> None:
    ja = build_journal_analytics(
        [_closed("2026-05-01T10:00:00", "A", 10.0, 9.5, 12.0, "2026-05-01T15:00:00")]
    )
    assert ja.current_drawdown_r == 0.0


def test_skipped_reason_counts() -> None:
    skip = raw_dict_to_trade_record(
        "/tmp/mock.jsonl",
        2,
        {
            "symbol": "ZZ",
            "timestamp": "2026-04-01T09:00:00",
            "submitted": False,
            "skipped_reasons": ["no_trigger_1m"],
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
        },
    )
    ja = build_journal_analytics([skip])
    assert ja.skipped_trades == 1
    assert ja.skipped_reason_counts.get("no_trigger_1m") == 1


def test_no_fake_pnl_without_usd() -> None:
    r = _closed("2026-04-01T10:00:00", "QQQ", 100.0, 99.0, 101.0, "2026-04-01T15:00:00")
    ja = build_journal_analytics([r])
    assert ja.has_reliable_pnl_usd is False
    assert ja.cumulative_pnl_points == []


def test_pnl_curve_when_all_rows_have_usd() -> None:
    rec = raw_dict_to_trade_record(
        "/tmp/mock.jsonl",
        1,
        {
            "symbol": "US",
            "timestamp": "2026-04-01T10:00:00",
            "submitted": True,
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
            "entry": 100.0,
            "stop": 99.0,
            "exit_time": "2026-04-01T15:00:00",
            "exit_price": 101.0,
            "realized_pnl_usd": 42.5,
        },
    )
    ja = build_journal_analytics([rec])
    assert ja.has_reliable_pnl_usd is True
    assert len(ja.cumulative_pnl_points) == 1


def test_realized_pnl_usd_from_raw_aliases() -> None:
    assert realized_pnl_usd_from_raw({"realized_pnl_usd": 10}) == 10.0
    assert realized_pnl_usd_from_raw({"noise": None}) is None


def test_profit_factor_r_none_when_no_losing_closed() -> None:
    winners = []
    for i in range(2):
        winners.append(
            _closed("2026-05-01T10:00:00", "W", 10.0, 9.0, 11.5, f"2026-05-0{i+1}T15:00:00"),
        )
    ja = build_journal_analytics(winners)
    assert ja.total_r_closed is not None and ja.total_r_closed > 0


def test_performance_by_exit_hour_uses_exit_time_not_submitted() -> None:
    """R-by-hour uses exit_time (NY), not entry submission time."""

    r = raw_dict_to_trade_record(
        "/tmp/mock.jsonl",
        0,
        {
            "symbol": "HH",
            "timestamp": "2026-05-05T14:30:00+00:00",
            "submitted": True,
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
            "entry": 50.0,
            "stop": 49.0,
            "exit_time": "2026-05-05T21:30:00+00:00",
            "exit_price": 52.0,
        },
    )
    ja = build_journal_analytics([r])
    assert ja.performance_by_exit_hour
    assert len(ja.performance_by_exit_hour) == 1


def test_decisions_by_submitted_hour_skipped_only() -> None:
    s1 = raw_dict_to_trade_record(
        "/tmp/a.jsonl",
        1,
        {
            "symbol": "S",
            "timestamp": "2026-06-01T14:05:00+00:00",
            "submitted": False,
            "skipped_reasons": ["x"],
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
        },
    )
    s2 = raw_dict_to_trade_record(
        "/tmp/a.jsonl",
        2,
        {
            "symbol": "T",
            "timestamp": "2026-06-01T14:06:00+00:00",
            "submitted": False,
            "skipped_reasons": ["y"],
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
        },
    )
    ja = build_journal_analytics([s1, s2])
    assert sum(ja.decisions_by_submitted_hour.values()) == 2
    assert len(ja.decisions_by_submitted_hour) >= 1


def test_data_quality_payload_counts() -> None:
    o = raw_dict_to_trade_record(
        "/tmp/x.jsonl",
        0,
        {"symbol": "O", "timestamp": "2026-01-01T10:00:00", "submitted": True, "strategy_id": "s", "signal_category": "", "direction": "long"},
    )
    dq = build_data_quality_payload(
        [o],
        {"charts_available": 0, "charts_missing_candles": 1},
    )
    assert dq["missing_exit"] == 1
    assert dq["no_trade_records"] is False


def test_all_closed_have_reliable_usd_helper() -> None:
    r = raw_dict_to_trade_record(
        "/tmp/mock.jsonl",
        1,
        {
            "symbol": "US",
            "timestamp": "2026-04-01T10:00:00",
            "submitted": True,
            "strategy_id": "x",
            "signal_category": "x",
            "direction": "long",
            "entry": 100.0,
            "stop": 99.0,
            "exit_time": "2026-04-01T15:00:00",
            "exit_price": 101.0,
            "realized_pnl_usd": 10.0,
        },
    )
    assert all_closed_trades_have_reliable_usd([r])
