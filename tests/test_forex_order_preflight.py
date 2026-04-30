"""Forex bracket preflight geometry (LONG/SHORT, LMT only)."""

from __future__ import annotations

from bot.forex.preflight import validate_bracket


def test_long_requires_stop_below_entry_below_target() -> None:
    assert validate_bracket(
        direction="long",
        entry=1.0622,
        stop=1.0600,
        target=1.0650,
        min_tick=0.00005,
        order_type="LMT",
    ).ok


def test_short_requires_target_below_entry_below_stop() -> None:
    assert validate_bracket(
        direction="short",
        entry=150.42,
        stop=151.10,
        target=148.05,
        min_tick=0.01,
        order_type="LMT",
    ).ok


def test_rejects_bad_long_geometry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bot.forex import preflight as pfmod

    def _bad_long(**_kw):
        from decimal import Decimal

        return Decimal("1"), Decimal("1"), Decimal("2")

    monkeypatch.setattr(pfmod, "round_bracket_prices_decimal", _bad_long)
    assert not validate_bracket(
        direction="long",
        entry=1.0,
        stop=1.05,
        target=1.02,
        min_tick=0.00005,
        order_type="LMT",
    ).ok


def test_rejects_market_order_type_strings() -> None:
    pf = validate_bracket(
        direction="long",
        entry=100.01,
        stop=99.9,
        target=101.02,
        min_tick=0.001,
        order_type="MKT",
    )
    assert "order_type_must_be_LMT" in "".join(pf.reasons)
