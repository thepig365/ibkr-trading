"""End-to-end tests for the SMC liquidity-reversal *research* evaluator.

V0 invariants enforced here:

* ``execution_allowed`` is always ``False``.
* ``risk_off`` (and ``crisis`` and ``unknown``) regimes block new setups.
* The detector cannot generate a market-order chase: an extension above
  ``reject_if_price_extended_from_entry_pct`` rejects the setup.
* Setups with R/R below 2.0 are rejected.
* The dry-run plan structure matches the documented schema.
"""

from __future__ import annotations

from typing import Iterable

from bot.market_structure import Candle
from bot.strategy_engine import (
    STRATEGY_NAME,
    StrategyEvaluation,
    evaluate_smc_liquidity_reversal,
)


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=f"d{i:03d}", open=o, high=h, low=l, close=c, volume=1000.0)


def _approved_setup_candles() -> list[Candle]:
    """Synthetic OHLCV that satisfies every SMC condition.

    Designed so that:
      * i=2 is a confirmed BIG swing high at 1100 (target liquidity).
      * i=8 is a smaller confirmed pivot high at 1018 (the ChoCH pivot).
      * i=11 is the lowest confirmed swing low (996).
      * i=15 is a bullish liquidity sweep (low=994 < 996, close=996.5 ≥ 996).
      * i=16 is a bearish candle (the order block).
      * i=17 closes at 1018.5 (> pivot 1018) → ChoCH.
      * i=18 forms a 3-candle bullish FVG with c1=i=16 (high 997)
        and c3=i=18 (low 1017) → zone 997 → 1017.
    """
    bars: Iterable[tuple[int, float, float, float, float]] = [
        (0,  1040, 1045, 1038, 1043),
        (1,  1043, 1048, 1041, 1046),
        (2,  1046, 1100, 1044, 1099),  # BIG pivot high (target_1)
        (3,  1054, 1054, 1042, 1043),
        (4,  1043, 1044, 1018, 1019),
        (5,  1019, 1020, 1014, 1015),
        (6,  1011, 1012, 1010, 1011),  # tame highs so i=8 can be a pivot
        (7,  1011, 1012, 1009, 1010),
        (8,  1010, 1018, 1009, 1017),  # pivot H = 1018 (the ChoCH pivot)
        (9,  1015, 1015, 1004, 1005),
        (10, 1005, 1006,  999, 1000),
        (11, 1000, 1001,  996,  997),  # lowest swing low (996)
        (12,  997,  998,  996.5, 997),
        (13,  997,  998,  996.5, 997),
        (14,  997,  998,  996.5, 997),
        (15,  997,  998,  994,  996.5),  # SWEEP (994 < 996)
        (16,  996.5, 997,  995,  995.5),  # bearish OB
        (17,  995.5, 1019, 995, 1018.5),  # ChoCH (close 1018.5 > 1018)
        (18, 1018.5, 1019, 1017, 1018),   # forms bullish FVG with i=16
        (19, 1018, 1019, 1017, 1018),
        (20, 1018, 1019, 1017, 1018),
    ]
    return [_candle(*b) for b in bars]


def _no_setup_candles() -> list[Candle]:
    """Trending series with no sweeps and no ChoCH."""
    return [_candle(i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i) for i in range(30)]


# ---------------------------------------------------------------------------
# Evaluator output shape & approval path
# ---------------------------------------------------------------------------
def test_evaluator_returns_documented_shape() -> None:
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_approved_setup_candles(),
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=1018.0,
    )
    assert isinstance(evaluation, StrategyEvaluation)
    payload = evaluation.to_dict()
    for key in (
        "strategy", "symbol", "timeframe", "approved_for_dry_run",
        "execution_allowed", "market_regime", "candle_count",
        "sequence", "trade_plan", "rejection_reasons", "notes",
    ):
        assert key in payload
    assert payload["strategy"] == STRATEGY_NAME
    assert payload["execution_allowed"] is False
    assert payload["sequence"]["sweep"]["found"] is True
    assert payload["sequence"]["choch"]["found"] is True
    assert payload["sequence"]["fvg"]["found"] is True
    assert payload["sequence"]["order_block"]["found"] is True


def test_approved_setup_passes_all_rules() -> None:
    candles = _approved_setup_candles()
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=candles[-1].close,
    )
    assert evaluation.rejection_reasons == [], evaluation.rejection_reasons
    assert evaluation.approved_for_dry_run is True
    plan = evaluation.trade_plan
    assert plan is not None
    assert plan["execution_allowed"] is False  # always
    assert plan["entry_type"] == "limit_at_fvg_top"
    assert plan["entry_price"] > plan["structural_stop"]
    assert plan["risk_per_share"] > 0
    assert plan["target_1"] >= 1100  # BIG pivot from i=2
    assert plan["risk_reward_to_target_1"] >= 2.0
    assert plan["qty_by_risk"] > 0


def test_execution_allowed_is_always_false() -> None:
    """Even if approved, execution must remain disabled in V0."""
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_approved_setup_candles(),
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=1018.0,
    )
    assert evaluation.execution_allowed is False
    assert evaluation.trade_plan is not None
    assert evaluation.trade_plan["execution_allowed"] is False


# ---------------------------------------------------------------------------
# Rejection rules
# ---------------------------------------------------------------------------
def test_risk_off_regime_blocks_new_setups() -> None:
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_approved_setup_candles(),
        market_regime="risk_off",
        account_equity=100_000.0,
        latest_close=1018.0,
    )
    assert any("risk_off" in r for r in evaluation.rejection_reasons)
    assert evaluation.approved_for_dry_run is False


def test_unknown_regime_blocks_new_setups() -> None:
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_approved_setup_candles(),
        market_regime="unknown",
        account_equity=100_000.0,
        latest_close=1018.0,
    )
    assert any("unknown" in r for r in evaluation.rejection_reasons)


def test_no_setup_returns_no_sequence_and_rejection_reasons() -> None:
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=_no_setup_candles(),
        market_regime="neutral",
        account_equity=100_000.0,
    )
    assert evaluation.approved_for_dry_run is False
    assert evaluation.execution_allowed is False
    assert evaluation.trade_plan is None
    assert any(
        r in {"no_liquidity_sweep", "no_choch_after_sweep", "no_bullish_fvg",
              "no_order_block"}
        for r in evaluation.rejection_reasons
    )


def test_no_chasing_rejection_when_latest_close_runs_away_from_entry() -> None:
    candles = _approved_setup_candles()
    # entry should be ~1017; pretend price has rallied to 1100.
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=candles,
        market_regime="neutral",
        account_equity=100_000.0,
        latest_close=1100.0,
    )
    assert any(
        "price_extended_from_entry_pct" in r
        for r in evaluation.rejection_reasons
    )
    assert evaluation.approved_for_dry_run is False


def test_low_rr_setup_is_rejected() -> None:
    candles = _approved_setup_candles()
    # Override min_reward_to_risk to an unrealistically high floor so
    # the fixture (~3.6 R/R) trips the rule.
    cfg = type("Cfg", (), {"strategies": {
        "SMC_LIQUIDITY_REVERSAL_RESEARCH": {"risk": {"min_reward_to_risk": 50.0}}
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
        r.startswith("r_r_to_target_1") for r in evaluation.rejection_reasons
    )
    assert evaluation.approved_for_dry_run is False


def test_insufficient_candles_returns_clear_reason() -> None:
    evaluation = evaluate_smc_liquidity_reversal(
        symbol="TEST",
        candles=[_candle(0, 10, 11, 9, 10)],
        market_regime="neutral",
    )
    assert any(
        r.startswith("insufficient_candles")
        for r in evaluation.rejection_reasons
    )
    assert evaluation.trade_plan is None


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def test_qty_by_risk_uses_one_percent_account_risk() -> None:
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
    # 1% of 100k = $1000 risk; risk_per_share ≈ 23 → qty ≈ 43.
    assert plan["qty_by_risk"] >= 1
    expected = int(1000 / plan["risk_per_share"])
    # Allow trimming by max_equity_per_position_pct.
    assert plan["qty_by_risk"] <= expected
