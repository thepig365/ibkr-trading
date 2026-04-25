"""Min-tick bracket normalization (Prompt 13J.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.execution.price_ticks import (
    MIN_TICK_US_STOCK_DEFAULT,
    normalize_bracket_prices,
    round_to_tick,
    validate_normalized_bracket,
)
from bot.strategies.ict_smc_intraday.model import DIRECTION_LONG, DIRECTION_SHORT


def test_round_to_tick_nearest_floor_ceil() -> None:
    t = Decimal("0.01")
    assert round_to_tick(Decimal("345.4167"), t, "nearest") == Decimal("345.42")
    assert round_to_tick(Decimal("345.414"), t, "nearest") == Decimal("345.41")
    assert round_to_tick(Decimal("345.4167"), t, "floor") == Decimal("345.41")
    assert round_to_tick(Decimal("345.4167"), t, "ceil") == Decimal("345.42")


def test_amd_stop_345_4167_normalizes_to_valid_001_grid() -> None:
    """Example from 13J: long stop 345.4167 -> floor to 345.41 at 0.01 tick."""
    n = normalize_bracket_prices(
        DIRECTION_LONG,
        346.63,
        345.4167,
        348.45,
        Decimal("0.01"),
        Decimal("1.2"),
    )
    assert n.valid
    assert n.stop == Decimal("345.41")
    assert n.entry == round_to_tick(Decimal("346.63"), Decimal("0.01"), "nearest")
    assert n.target == Decimal("348.45")
    assert float(n.planned_rr_after or 0) >= 1.2


def test_long_stop_floor_target_ceil_ordering() -> None:
    n = normalize_bracket_prices(
        DIRECTION_LONG,
        100.0,
        99.333,
        102.777,
        Decimal("0.01"),
        Decimal("1.0"),
    )
    assert n.valid
    assert n.stop < n.entry < n.target


def test_short_bracket_normalization_geometry() -> None:
    n = normalize_bracket_prices(
        DIRECTION_SHORT,
        200.0,
        202.333,
        196.777,
        Decimal("0.01"),
        Decimal("1.0"),
    )
    assert n.valid
    assert n.target < n.entry < n.stop


def test_rr_recalculated_after_rounding() -> None:
    n = normalize_bracket_prices(
        DIRECTION_LONG,
        100.0,
        99.0,
        102.0,
        Decimal("0.01"),
        Decimal("1.0"),
    )
    assert n.valid
    assert n.planned_rr_before is not None and n.planned_rr_after is not None
    assert n.planned_rr_after == Decimal("2")


def test_rr_below_min_rejects() -> None:
    n = normalize_bracket_prices(
        DIRECTION_LONG,
        100.0,
        99.9,
        100.05,
        Decimal("0.01"),
        Decimal("5.0"),
    )
    assert not n.valid
    assert any("rr_below" in r for r in n.rejection_reasons)


def test_non_positive_min_tick_uses_default_us_001() -> None:
    n = normalize_bracket_prices(
        DIRECTION_LONG,
        100.0,
        99.0,
        102.0,
        Decimal("-1"),
        Decimal("1.0"),
    )
    assert n.valid
    assert n.min_tick == MIN_TICK_US_STOCK_DEFAULT


def test_validate_normalized_bracket_short() -> None:
    ok, reasons, rr = validate_normalized_bracket(
        DIRECTION_SHORT,
        Decimal("200"),
        Decimal("202"),
        Decimal("196"),
        Decimal("1.0"),
    )
    assert ok and not reasons
    assert rr == Decimal("2")

