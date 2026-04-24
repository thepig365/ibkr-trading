"""Tests for the structural stop math and the max-stop-distance gate."""

from __future__ import annotations

import pytest

from bot.market_structure import calculate_structural_stop
from bot.strategy_engine import (
    DEFAULT_STRATEGY_CFG,
    evaluate_smc_liquidity_reversal,
)
from tests.test_smc_liquidity_reversal import _approved_setup_candles


def test_structural_stop_uses_lowest_of_sweep_and_ob_minus_buffer() -> None:
    setup = {"sweep": {"sweep_low": 994.0}, "order_block": {"low": 995.0}}
    stop = calculate_structural_stop(setup, buffer_cents=0.05)
    assert stop == 993.95


def test_structural_stop_buffer_is_subtracted() -> None:
    setup = {"sweep": {"sweep_low": 50.0}, "order_block": {"low": 49.5}}
    assert calculate_structural_stop(setup, buffer_cents=0.10) == 49.40


def test_evaluator_rejects_setups_with_stop_distance_above_5_pct() -> None:
    candles = _approved_setup_candles()
    # Tighten max stop distance well below the 2% the fixture produces.
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {"stop": {"max_allowed_stop_pct": 0.5}}
    }})()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        cfg=cfg,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    assert any(
        r.startswith("stop_distance_pct") for r in evaluation.rejection_reasons
    )
    assert evaluation.approved_for_dry_run is False
    assert evaluation.execution_allowed is False


def test_default_max_stop_pct_is_5() -> None:
    assert DEFAULT_STRATEGY_CFG["stop"]["max_allowed_stop_pct"] == 5.0


def test_calculate_structural_stop_requires_at_least_one_leg() -> None:
    with pytest.raises(ValueError):
        calculate_structural_stop({"sweep": {}, "order_block": {}})
