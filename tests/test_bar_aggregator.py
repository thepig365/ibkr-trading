"""5-second to 1-minute aggregator tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from backend.data.bar_aggregator import BarAggregator

NY = pytz.timezone("America/New_York")


def _start() -> datetime:
    return NY.localize(datetime(2026, 4, 29, 10, 0, 0))


def test_emits_completed_minute_on_boundary() -> None:
    agg = BarAggregator()
    base = _start()
    for i in range(12):
        result = agg.ingest(
            "SPY",
            base + timedelta(seconds=i * 5),
            open_=500 + 0.01 * i,
            high=500.5 + 0.01 * i,
            low=499.5,
            close=500.2 + 0.01 * i,
            volume=100,
        )
        assert result is None

    completed = agg.ingest(
        "SPY",
        base + timedelta(minutes=1),
        open_=500.5,
        high=501.0,
        low=500.2,
        close=500.7,
        volume=120,
    )
    assert completed is not None
    assert completed.symbol == "SPY"
    assert completed.timestamp == base
    assert completed.volume == 1200
    assert completed.open == 500.0
    assert completed.high == 500.61
    assert completed.low == 499.5


def test_aggregator_ignores_late_5s_bar() -> None:
    agg = BarAggregator()
    base = _start()
    agg.ingest("SPY", base + timedelta(minutes=1), 1, 1, 1, 1, 0)
    out = agg.ingest("SPY", base, 99, 99, 99, 99, 100)
    assert out is None
