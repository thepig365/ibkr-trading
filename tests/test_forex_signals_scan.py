"""Heuristic ICT scan on synthetic 1m bars."""

from __future__ import annotations

from bot.backtests.candle_cache import BarRow
from bot.forex.signals import simple_fx_ict_scan


def test_scan_produces_flat_or_trade_direction() -> None:
    bars = [
        BarRow(
            timestamp=f"2026-04-26 10:{i:02d}:00+00",
            open=1.0 + i * 0.001,
            high=1.01 + i * 0.001,
            low=0.99 + i * 0.001,
            close=1.005 + i * 0.001,
            volume=0,
        )
        for i in range(20)
    ]
    sig = simple_fx_ict_scan("AUD/USD", bars)
    assert sig is not None
    assert getattr(sig, "direction", None) is not None
