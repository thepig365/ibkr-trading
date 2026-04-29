"""Forex notional caps (pure logic)."""

from __future__ import annotations

from pathlib import Path

from bot.forex.daily_notional import can_add_notional


def test_daily_cap_blocks_over_100k(tmp_path: Path) -> None:
    ok3, r3 = can_add_notional(
        tmp_path,
        pair_slug="USDJPY",
        usd_estimate=110_000.0,
        max_daily_usd=100_000.0,
        max_pair_usd=200_000.0,
        timezone_name="Australia/Melbourne",
    )
    assert ok3 is False
    assert r3 == "max_daily_notional_usd"


def test_per_pair_cap_blocks(tmp_path: Path) -> None:
    ok, rs = can_add_notional(
        tmp_path,
        pair_slug="AUDUSD",
        usd_estimate=35_000.0,
        max_daily_usd=300_000.0,
        max_pair_usd=30_000.0,
        timezone_name="Australia/Melbourne",
    )
    assert ok is False
    assert rs == "per_pair_notional_usd_cap"
