"""Unit tests for ``bot.market_structure`` primitives.

The tests construct deterministic OHLCV sequences so each detector can
be exercised in isolation.
"""

from __future__ import annotations

from typing import Iterable

from bot.market_structure import (
    Candle,
    calculate_structural_stop,
    detect_bullish_fvg,
    detect_bullish_order_block,
    detect_choch_after_sweep,
    detect_liquidity_sweep,
    detect_swing_highs,
    detect_swing_lows,
    prior_swing_high_target,
    select_fvg_for_setup,
)


def _candles(rows: Iterable[tuple[float, float, float, float]]) -> list[Candle]:
    out: list[Candle] = []
    for i, (o, h, l, c) in enumerate(rows):
        out.append(
            Candle(
                timestamp=f"d{i:02d}",
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=1000.0,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Swing high / low
# ---------------------------------------------------------------------------
def test_detect_swing_highs_basic() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 15, 10, 14),  # i=2 pivot high (15)
        (14, 13, 12, 12),
        (12, 12, 10, 11),
    ])
    swings = detect_swing_highs(candles, left=2, right=2)
    assert len(swings) == 1
    s = swings[0]
    assert s.type == "swing_high"
    assert s.index == 2
    assert s.price == 15.0
    assert s.confirmed is True


def test_detect_swing_lows_basic() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 5, 7),     # i=2 pivot low (5)
        (7, 9, 6, 8),
        (8, 10, 7, 9),
    ])
    swings = detect_swing_lows(candles, left=2, right=2)
    assert len(swings) == 1
    assert swings[0].type == "swing_low"
    assert swings[0].index == 2
    assert swings[0].price == 5.0


def test_swings_are_not_confirmed_at_the_right_edge() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 15, 10, 14),  # would be pivot but no right_bars yet
        (14, 13, 12, 12),  # only 1 right bar
    ])
    confirmed = detect_swing_highs(candles, left=2, right=2)
    assert confirmed == []  # no lookahead leakage in live mode

    provisional = detect_swing_highs(
        candles, left=2, right=2, allow_unconfirmed=True
    )
    assert len(provisional) == 1
    assert provisional[0].confirmed is False


def test_detect_swing_highs_rejects_equal_neighbours() -> None:
    # Strictly-greater rule: equal highs do not qualify.
    candles = _candles([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 12, 10, 11),  # tie with i=1 high → not a pivot
        (11, 11, 9, 10),
        (10, 11, 9, 10),
    ])
    assert detect_swing_highs(candles) == []


# ---------------------------------------------------------------------------
# Liquidity sweep
# ---------------------------------------------------------------------------
def test_detect_liquidity_sweep_requires_close_back_above() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 12, 8, 9),     # i=2 confirmed swing low (8)
        (9, 10, 8.5, 9),
        (9, 10, 8.5, 9),
        (9, 10, 7, 9),      # i=5 sweeps (7 < 8) and closes back above (9 ≥ 8)
        (9, 10, 9, 9.5),
    ])
    sweeps = detect_liquidity_sweep(candles)
    assert len(sweeps) == 1
    s = sweeps[0]
    assert s["index"] == 5
    assert s["sweep_low"] == 7.0
    assert s["swept_low_price"] == 8.0
    assert s["closed_back_above"] is True


def test_detect_liquidity_sweep_rejects_close_below_swept_low() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 12, 8, 9),     # confirmed swing low at 8
        (9, 10, 8.5, 9),
        (9, 10, 8.5, 9),
        (9, 10, 7, 7.5),    # closes BELOW the swept low → rejected
    ])
    sweeps = detect_liquidity_sweep(candles)
    assert sweeps == []


# ---------------------------------------------------------------------------
# Change of character
# ---------------------------------------------------------------------------
def test_detect_choch_only_after_sweep_and_only_on_close() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 15, 9, 14),    # pivot high = 15 at i=2
        (14, 13, 12, 12),
        (12, 12, 10, 11),
        (11, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 8, 9),     # confirmed swing low = 8 at i=7
        (9, 10, 8.5, 9),
        (9, 10, 8.5, 9),
        (9, 10, 7, 9),      # sweep at i=10 (low 7 < 8, close 9 ≥ 8)
        (9, 16, 9, 14),     # i=11 wicks above pivot but closes BELOW 15
        (14, 17, 13, 16),   # i=12 closes 16 > pivot 15 → ChoCH
    ])
    sweeps = detect_liquidity_sweep(candles)
    assert sweeps and sweeps[-1]["index"] == 10

    choch = detect_choch_after_sweep(candles, sweeps[-1])
    assert choch is not None
    assert choch["index"] == 12  # wick-only candle 11 was rejected
    assert choch["pivot_high_broken"] == 15.0
    assert choch["close"] == 16.0


def test_detect_choch_returns_none_when_no_sweep() -> None:
    candles = _candles([
        (10, 12, 9, 11),
        (11, 14, 10, 13),
    ])
    assert detect_choch_after_sweep(candles, sweep_event=None) is None


# ---------------------------------------------------------------------------
# Bullish FVG
# ---------------------------------------------------------------------------
def test_detect_bullish_fvg_requires_strict_gap() -> None:
    candles = _candles([
        (10, 11, 9, 10),    # c1
        (11, 13, 10, 12),   # c2
        (13, 15, 12, 14),   # c3 low 12 > c1 high 11 → bullish FVG
    ])
    fvgs = detect_bullish_fvg(candles)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg["low"] == 11.0
    assert fvg["high"] == 12.0
    assert fvg["start_index"] == 0
    assert fvg["end_index"] == 2
    assert fvg["size_pct"] > 0


def test_detect_bullish_fvg_rejects_overlap() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 12, 9, 11),
        (11, 12, 10, 11),  # c3.low 10 ≤ c1.high 11 → no FVG
    ])
    assert detect_bullish_fvg(candles) == []


# ---------------------------------------------------------------------------
# Bullish order block
# ---------------------------------------------------------------------------
def test_detect_bullish_order_block_picks_last_down_close_before_choch() -> None:
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10.5),
        (10.5, 11, 9, 9.5),  # bearish (close < open)
        (9.5, 13, 9.5, 12),  # bullish impulse (= candle prior to ChoCH)
        (12, 14, 12, 13),    # ChoCH candle (index = 4)
    ])
    choch = {"index": 4}
    ob = detect_bullish_order_block(candles, choch)
    assert ob is not None
    assert ob["index"] == 2
    assert ob["close"] < ob["open"]


def test_detect_bullish_order_block_returns_none_when_only_bullish_candles() -> None:
    candles = _candles([
        (10, 11, 9, 10.5),
        (10.5, 12, 10, 11.5),
        (11.5, 13, 11, 12.5),
        (12.5, 14, 12, 13.5),  # ChoCH candle
    ])
    ob = detect_bullish_order_block(candles, {"index": 3})
    assert ob is None


# ---------------------------------------------------------------------------
# Structural stop and prior swing-high target
# ---------------------------------------------------------------------------
def test_calculate_structural_stop_uses_min_minus_buffer() -> None:
    setup = {
        "sweep": {"sweep_low": 100.0},
        "order_block": {"low": 99.5},
    }
    assert calculate_structural_stop(setup, buffer_cents=0.05) == 99.45


def test_calculate_structural_stop_handles_missing_leg() -> None:
    setup = {"sweep": {"sweep_low": 100.0}, "order_block": None}
    assert calculate_structural_stop(setup, buffer_cents=0.0) == 100.0


def test_calculate_structural_stop_raises_when_both_missing() -> None:
    import pytest

    with pytest.raises(ValueError):
        calculate_structural_stop({"sweep": {}, "order_block": {}})


def test_prior_swing_high_target_returns_highest_pivot_before_sweep() -> None:
    # Two confirmed pivot highs before the sweep: a small one and a big one.
    # The target should be the higher one (buy-side liquidity).
    candles = _candles([
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 30, 9, 29),  # i=2 BIG pivot
        (29, 28, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 15, 9, 14),  # i=7 small pivot
        (14, 13, 9, 10),
        (10, 11, 9, 10),
    ])
    swings = detect_swing_highs(candles)
    assert {s.index for s in swings} == {2, 7}
    target = prior_swing_high_target(
        candles, sweep_event={"index": 9}, swings_high=swings
    )
    assert target == 30.0


# ---------------------------------------------------------------------------
# select_fvg_for_setup
# ---------------------------------------------------------------------------
def test_select_fvg_for_setup_filters_min_size_and_distance() -> None:
    candles = _candles([
        # Pre-impulse noise.
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        # tiny FVG (would be selected if not filtered)
        (10, 11.01, 9, 10),
        (10, 11.02, 9, 10),
        (10, 11.03, 9, 10),
    ])
    fake_sweep = {"index": 0}
    fake_choch = {"index": 4}
    fvg = select_fvg_for_setup(
        candles,
        fake_sweep,
        fake_choch,
        min_size_pct=10.0,  # impossibly large → filtered out
        max_distance_from_choch_bars=5,
    )
    assert fvg is None
