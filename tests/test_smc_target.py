"""Tests for the V1 target-1 selector and the ``target_debug`` payload.

The V0 behaviour picked the **highest** confirmed swing high in the
full history, which produced unrealistic R/R ratios on extended
uptrends (e.g. TSLA ≈ 5.95). V1 uses the *nearest* buy-side liquidity
above entry that still satisfies ``min_risk_reward`` and fits inside
``max_target_distance_pct``.
"""

from __future__ import annotations

from typing import Any

import pytest

from bot.market_structure import Candle, SwingPoint, select_target_1
from bot.strategy_engine import evaluate_smc_liquidity_reversal
from tests.test_smc_liquidity_reversal import _approved_setup_candles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cfg(overrides: dict[str, Any]) -> Any:
    return type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": overrides
    }})()


def _sweep_event(index: int) -> dict[str, Any]:
    return {"index": index, "timestamp": f"d{index:03d}"}


# ---------------------------------------------------------------------------
# Unit tests for select_target_1
# ---------------------------------------------------------------------------
def test_select_target_1_prefers_nearest_buy_side_liquidity() -> None:
    # Flat highs so range_high never sneaks in as a nearer target.
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=99.5,
               low=99.0, close=99.5)
        for i in range(40)
    ]
    swings = [
        SwingPoint(type="swing_high", index=5, timestamp="d005",
                   price=112.0, left_bars=2, right_bars=2, confirmed=True),
        SwingPoint(type="swing_high", index=15, timestamp="d015",
                   price=108.0, left_bars=2, right_bars=2, confirmed=True),
        SwingPoint(type="swing_high", index=25, timestamp="d025",
                   price=150.0, left_bars=2, right_bars=2, confirmed=True),
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(30), None,
        entry_price=100.0, risk_per_share=2.0,
        swings_high=swings,
        lookback_bars_before_sweep=60,
        max_target_distance_pct=25.0,
        min_risk_reward=2.0,
    )
    assert reason is None
    # Nearest above entry is 108 (R/R=4 ≥ 2), not the bigger 112 or
    # the faraway 150 that the V0 "highest pivot" rule used to prefer.
    assert target == pytest.approx(108.0)
    selected = [c for c in candidates if c["selected"]]
    assert len(selected) == 1 and selected[0]["price"] == pytest.approx(108.0)


def test_select_target_1_skips_candidates_below_min_rr() -> None:
    """Nearest pivot is too close: skip to the next pivot that passes R/R."""
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=101.0,
               low=99.0, close=100.5) for i in range(40)
    ]
    swings = [
        SwingPoint(type="swing_high", index=5, timestamp="d005",
                   price=101.0, left_bars=2, right_bars=2, confirmed=True),
        SwingPoint(type="swing_high", index=15, timestamp="d015",
                   price=110.0, left_bars=2, right_bars=2, confirmed=True),
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(30), None,
        entry_price=100.0, risk_per_share=2.0,
        swings_high=swings,
        max_target_distance_pct=25.0, min_risk_reward=2.0,
    )
    assert reason is None
    assert target == pytest.approx(110.0)
    assert any(
        c["selected"] and c["price"] == pytest.approx(110.0) for c in candidates
    )


def test_select_target_1_rejects_when_no_candidate_above_entry() -> None:
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=100.0, low=99.0, close=99.5)
        for i in range(10)
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(5), None,
        entry_price=200.0, risk_per_share=2.0,
        swings_high=[],
    )
    assert target is None
    assert reason == "no_target_above_entry"
    assert candidates == []


def test_select_target_1_rejects_when_beyond_max_distance() -> None:
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=200.0, low=99.0, close=150.0)
        for i in range(10)
    ]
    swings = [
        SwingPoint(type="swing_high", index=3, timestamp="d003",
                   price=200.0, left_bars=2, right_bars=2, confirmed=True),
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(5), None,
        entry_price=100.0, risk_per_share=2.0,
        swings_high=swings,
        max_target_distance_pct=25.0, min_risk_reward=2.0,
    )
    assert target is None
    assert reason == "target_1_too_far"
    # Debug list still surfaces the (rejected) candidate.
    assert any(c["type"] == "swing_high" for c in candidates)


def test_select_target_1_ignores_highest_high_when_out_of_lookback() -> None:
    """The V0 rule would have picked the far-away high; V1 must not."""
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=101.0,
               low=99.0, close=100.5) for i in range(300)
    ]
    # Very old high from outside the 60-bar window.
    swings = [
        SwingPoint(type="swing_high", index=10, timestamp="d010",
                   price=500.0, left_bars=2, right_bars=2, confirmed=True),
        # Recent, realistic target inside the lookback.
        SwingPoint(type="swing_high", index=260, timestamp="d260",
                   price=108.0, left_bars=2, right_bars=2, confirmed=True),
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(280), None,
        entry_price=100.0, risk_per_share=2.0,
        swings_high=swings,
        lookback_bars_before_sweep=60,
        max_target_distance_pct=25.0,
        min_risk_reward=2.0,
    )
    assert reason is None
    assert target == pytest.approx(108.0)
    assert all(c["price"] != 500.0 for c in candidates), (
        "the 500 high sits outside the 60-bar lookback and must not appear"
    )


def test_select_target_1_range_high_used_when_no_swings() -> None:
    candles = [
        Candle(timestamp=f"d{i:03d}", open=100.0, high=105.0 + (i == 5) * 10,
               low=99.0, close=100.5) for i in range(20)
    ]
    target, candidates, reason = select_target_1(
        candles, _sweep_event(15), None,
        entry_price=100.0, risk_per_share=2.0,
        swings_high=[],
        lookback_bars_before_sweep=60, max_target_distance_pct=25.0,
        min_risk_reward=2.0,
    )
    assert reason is None
    assert target == pytest.approx(115.0)
    assert any(c["type"] == "range_high" for c in candidates)


# ---------------------------------------------------------------------------
# End-to-end evaluator behaviour
# ---------------------------------------------------------------------------
def test_evaluator_target_debug_always_present() -> None:
    """Even rejected/incomplete setups must still include a target_debug stub."""
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_approved_setup_candles(),
        market_regime="risk_off",  # regime rejection, plan still built
        account_equity=100_000.0,
        latest_close=1018.0,
    )
    payload = evaluation.to_dict()
    assert "target_debug" in payload
    debug = payload["target_debug"]
    assert debug["method"] == "nearest_buy_side_liquidity"
    assert isinstance(debug["candidates"], list)


def test_evaluator_target_1_not_above_entry_rejection() -> None:
    """When every candidate is <= entry we must surface the proper reason."""
    candles = _approved_setup_candles()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        cfg=_cfg({"target": {"max_target_distance_pct": 0.05}}),
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    assert any(
        r.startswith("target_1_too_far") for r in evaluation.rejection_reasons
    )
    # The candidate list is still populated for visual review.
    assert evaluation.target_debug["candidates"], evaluation.target_debug


def test_evaluator_target_debug_candidate_shape() -> None:
    candles = _approved_setup_candles()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    debug = evaluation.to_dict()["target_debug"]
    assert debug["rejection_reason"] is None
    assert debug["candidates"], debug
    cand0 = debug["candidates"][0]
    for key in (
        "timestamp", "price", "type", "distance_pct_from_entry",
        "risk_reward", "selected",
    ):
        assert key in cand0
    selected = [c for c in debug["candidates"] if c["selected"]]
    assert len(selected) == 1


def test_evaluator_uses_nearest_not_highest() -> None:
    """Regression test for the TSLA case: a distant high outside the
    lookback window must not win when a closer, more realistic target
    exists inside the window."""
    candles = _approved_setup_candles()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        # Tighten the distance ceiling to 10%; the 1100 pivot (~8.16%
        # above entry) still qualifies, any farther outlier would not.
        cfg=_cfg({"target": {"max_target_distance_pct": 10.0}}),
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    plan = evaluation.trade_plan
    assert plan is not None, evaluation.rejection_reasons
    assert plan["target_1"] is not None
    assert plan["target_1"] <= 1100
    assert plan["target_1"] >= plan["entry_price"]
    # And no candidate past the 10% cap was ever selected.
    for c in plan["target_debug"]["candidates"]:
        if c["selected"]:
            assert c["distance_pct_from_entry"] <= 10.0


def test_evaluator_target_1_must_be_above_entry() -> None:
    candles = _approved_setup_candles()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    plan = evaluation.trade_plan
    assert plan is not None
    assert plan["target_1"] > plan["entry_price"]


def test_evaluator_rr_gate_enforced() -> None:
    candles = _approved_setup_candles()
    cfg = _cfg({
        "risk": {"min_reward_to_risk": 100.0},
        "target": {"min_risk_reward": 100.0},
    })
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        cfg=cfg,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    assert evaluation.approved_for_dry_run is False
    assert any(
        "r_r_to_target_1" in r for r in evaluation.rejection_reasons
    )
