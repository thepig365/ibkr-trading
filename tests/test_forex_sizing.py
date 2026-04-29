"""Forex sizing differs from equity share sizing."""

from __future__ import annotations

from bot.forex.pairs import parse_pair, pip_size_for_pair
from bot.forex.sizing import estimate_units_for_risk


def test_units_audusd_scaling() -> None:
    spec = parse_pair("AUD/USD")
    sr = estimate_units_for_risk(
        spec,
        equity_usd=100_000,
        risk_pct=0.05,
        entry=1.0500,
        stop=1.0480,
        max_units=500_000,
        pip_size=pip_size_for_pair(spec),
    )
    assert sr.units >= 25000


def test_jpy_quote_pip_estimate() -> None:
    spec = parse_pair("USD/JPY")
    sr = estimate_units_for_risk(
        spec,
        equity_usd=50_000,
        risk_pct=0.1,
        entry=150.05,
        stop=149.85,
        max_units=200_000,
        pip_size=pip_size_for_pair(spec),
    )
    assert sr.units > 0
