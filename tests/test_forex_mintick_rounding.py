"""Forex Decimal tick snapping (no IEEE float tails)."""

from __future__ import annotations

from decimal import Decimal

from bot.forex.ticks import MinTickResolution, round_bracket_prices_decimal
from bot.forex.ticks import decimal_price
from bot.forex.ticks import resolve_forex_min_tick
from bot.forex.ticks import round_price_to_tick


def test_audusd_float_tail_target_snaps_cleanly() -> None:
    mt = Decimal("0.00005")
    tgt_raw = Decimal("0.7126009999999999")
    snapped = round_price_to_tick(tgt_raw, mt, "nearest")
    assert snapped == Decimal("0.71260")

    mt = Decimal("0.00005")
    e, s_, t = round_bracket_prices_decimal(
        direction="long",
        entry="0.712325",
        stop="0.712179",
        target="0.7126009999999999",
        min_tick=mt,
        entry_mode="nearest",
    )
    assert e % mt == 0
    assert s_ % mt == 0
    assert t % mt == 0
    assert s_ < e < t


def test_usdjpy_uses_standard_jpy_resolution_path() -> None:
    res = resolve_forex_min_tick("USD/JPY", contract_details=None, fallback_config=None)
    assert res.source == "fallback"
    assert res.min_tick == Decimal("0.01")


def test_config_override_non_jpy_min_tick() -> None:
    cfg = {"forex_tick_fallback": {"non_jpy_quote": "0.0001"}}
    r = resolve_forex_min_tick("AUD/USD", contract_details=None, fallback_config=cfg)
    assert r.min_tick == Decimal("0.0001")
    assert r.source == "config"


def test_decimal_price_handles_string() -> None:
    assert decimal_price("150.055") == Decimal("150.055")
