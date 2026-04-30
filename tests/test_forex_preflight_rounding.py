"""Rounded-bracket preflight geometry."""

from __future__ import annotations

from decimal import Decimal

from bot.forex.preflight import preflight_rounded_forex_bracket
from bot.forex.ticks import MinTickResolution


def test_short_geometry_target_below_entry_below_stop_after_round() -> None:
    tick = MinTickResolution(Decimal("0.01"), "fallback")
    p = preflight_rounded_forex_bracket(
        direction="short",
        original_entry="150.42",
        original_stop="151.10",
        original_target="148.05",
        tick=tick,
        order_type="LMT",
    )
    assert p.geometry_ok
    assert Decimal(p.rounded_stop) > Decimal(p.rounded_entry) > Decimal(p.rounded_target)


def test_long_rounded_stop_below_entry_below_target() -> None:
    tick = MinTickResolution(Decimal("0.00005"), "fallback")
    p = preflight_rounded_forex_bracket(
        direction="long",
        original_entry=1.0622,
        original_stop=1.0600,
        original_target=1.0650,
        tick=tick,
        order_type="LMT",
    )
    assert p.ok
    rs, re_, rt = Decimal(p.rounded_stop), Decimal(p.rounded_entry), Decimal(p.rounded_target)
    assert rs < re_ < rt


def test_market_disallowed() -> None:
    tick = MinTickResolution(Decimal("0.01"), "fallback")
    p = preflight_rounded_forex_bracket(
        direction="long",
        original_entry="1.00",
        original_stop="0.95",
        original_target="1.05",
        tick=tick,
        order_type="MKT",
    )
    assert not p.ok


def test_invalid_geometry_blocks_with_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tick = MinTickResolution(Decimal("0.00005"), "fallback")

    def _collapsed(**_kw):
        return Decimal("1"), Decimal("1"), Decimal("2")

    monkeypatch.setattr(
        "bot.forex.preflight.round_bracket_prices_decimal",
        _collapsed,
    )
    p = preflight_rounded_forex_bracket(
        direction="long",
        original_entry="1",
        original_stop="0.9",
        original_target="1.1",
        tick=tick,
        order_type="LMT",
    )
    assert not p.ok
    assert "forex_invalid_rounded_bracket_geometry" in p.reasons