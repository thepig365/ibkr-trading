"""ICT Fair Value Gap detection + retest entry tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytz

from backend.config import load_config
from backend.strategy.ict_strategy import DailyBiasResult, ICTStrategy
from backend.strategy.models import Bar, Direction

NY = pytz.timezone("America/New_York")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.example.yaml"


def _bar(symbol: str, ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1m",
        timestamp=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000,
    )


def test_bullish_fvg_detected_with_long_bias() -> None:
    cfg = load_config(CONFIG_PATH)
    s = ICTStrategy(cfg)
    s.set_daily_bias(
        "SPY",
        DailyBiasResult(
            direction=Direction.LONG,
            equilibrium=499.0,
            swing_high=510.0,
            swing_low=490.0,
            target_price=510.0,
            confidence=1.0,
            reason="discount-zone",
        ),
    )

    base = NY.localize(datetime(2026, 4, 29, 10, 0))
    bars = [
        _bar("SPY", base + timedelta(minutes=0), 500.0, 500.5, 499.5, 500.2),  # K1.high=500.5
        _bar("SPY", base + timedelta(minutes=1), 500.2, 500.6, 500.0, 500.5),  # K2 displacement
        _bar("SPY", base + timedelta(minutes=2), 500.7, 502.0, 500.7, 501.5),  # K3.low=500.7 > K1.high=500.5
        _bar("SPY", base + timedelta(minutes=3), 501.5, 501.8, 500.8, 501.6),
    ]
    for bar in bars:
        s.on_bar(bar, {"daily_bias": {}, "news_blackout": {}})

    state = s._states["SPY"]
    assert any(fvg.direction is Direction.LONG for fvg in state.fvgs)


def test_retest_emits_signal_with_long_bias() -> None:
    cfg = load_config(CONFIG_PATH)
    s = ICTStrategy(cfg)
    s.set_daily_bias(
        "SPY",
        DailyBiasResult(
            direction=Direction.LONG,
            equilibrium=499.0,
            swing_high=520.0,
            swing_low=480.0,
            target_price=520.0,
            confidence=1.0,
            reason="discount-zone",
        ),
    )

    base = NY.localize(datetime(2026, 4, 29, 10, 5))
    setup_bars = [
        _bar("SPY", base + timedelta(minutes=0), 500.0, 500.5, 499.5, 500.2),
        _bar("SPY", base + timedelta(minutes=1), 500.2, 500.6, 500.0, 500.5),
        _bar("SPY", base + timedelta(minutes=2), 500.7, 502.0, 500.7, 501.5),
    ]
    signal = None
    for bar in setup_bars:
        signal = s.on_bar(bar, {"daily_bias": {}, "news_blackout": {}}) or signal

    retest = _bar(
        "SPY", base + timedelta(minutes=3), 501.5, 501.8, 500.5, 501.4
    )  # touches FVG.top=500.7, closes above midpoint=500.6
    signal = s.on_bar(retest, {"daily_bias": {}, "news_blackout": {}})

    assert signal is not None
    assert signal.direction is Direction.LONG
    assert signal.fvg_top is not None and signal.fvg_bottom is not None
    rr = abs(signal.take_profit - signal.entry_price) / abs(
        signal.entry_price - signal.stop_loss
    )
    assert rr >= cfg.risk.min_rr_ratio
