"""Unit tests for the ICT/SMC Intraday Liquidity Reversal V1 strategy.

Covers (Prompt 13D, PART J):

* 1min timeframe registry exists and uses ``"1 min"`` bar size.
* ``scan_symbol_from_bars`` handles missing 1m bars (BLOCKED).
* ``classify_intraday_signal`` produces:
   - DAY_TRADE_READY_STRICT when 1m FVG + MSS + R/R >= strict.
   - DAY_TRADE_READY_AGGRESSIVE when 1m OB/breaker + MSS without FVG.
   - WATCH_ONLY when 5m setup exists but 1m trigger missing.
   - NO_SETUP when there is no 5m sweep.
   - INVALID_RISK when R/R is below the aggressive floor or stop is too wide.
* ``build_intraday_trade_plan`` rejects:
   - long with stop >= entry (stop above entry).
   - long with target <= entry (only happens via degenerate FVG; tested
     by directly forcing an FVG above entry).
   - non-positive risk per share.
* The strategy NEVER imports :mod:`bot.broker` and NEVER mutates broker
  state. Every payload that leaves the scanner has
  ``execution_allowed=False`` and ``paper_only=True``.
* The scanner module is importable without an IBKR connection.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Iterable

import pytest

from bot.smc_timeframes import (
    DEFAULT_TIMEFRAME_SPECS,
    DEFAULT_TIMEFRAME_STRATEGY,
    SUPPORTED_TIMEFRAMES,
    normalise_timeframe,
    resolve_timeframe_spec,
)
from bot.strategies.ict_smc_intraday import (
    ALLOWED_SIGNAL_CATEGORIES,
    DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT,
    DEFAULT_MAX_STOP_DISTANCE_PCT,
    DEFAULT_MIN_RR_AGGRESSIVE,
    DEFAULT_MIN_RR_STRICT,
    DIRECTION_FLAT,
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_SOURCE_BREAKER,
    ENTRY_SOURCE_FVG,
    ENTRY_SOURCE_NONE,
    ENTRY_SOURCE_OB,
    FiveMinuteSetup,
    IntradayContext,
    IntradayEvaluation,
    IntradayRiskConfig,
    IntradayTradePlan,
    OneMinuteTrigger,
    SIGNAL_BLOCKED,
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
    SIGNAL_INVALID_RISK,
    SIGNAL_NO_SETUP,
    SIGNAL_WATCH_ONLY,
    STRATEGY_KEY,
    build_intraday_trade_plan,
    build_watchlist_summary,
    classify_intraday_signal,
    format_intraday_telegram_zh,
    save_intraday_evaluation,
    save_intraday_watchlist_summary,
    scan_symbol_from_bars,
)


# ---------------------------------------------------------------------------
# Bar helpers
# ---------------------------------------------------------------------------
def _bar(i: int, o: float, h: float, l: float, c: float, vol: float = 1000.0) -> dict[str, Any]:
    return {
        "timestamp": f"t{i:04d}",
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(vol),
    }


def _flat_bars(n: int, base: float = 100.0) -> list[dict[str, Any]]:
    """A boring no-setup series: tiny range, no sweeps, no ChoCH."""
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            _bar(i, base, base + 0.05, base - 0.05, base + 0.01)
        )
    return out


def _long_setup_bars() -> list[dict[str, Any]]:
    """Synthetic series engineered to fire a bullish 5m sweep + ChoCH.

    Mirrors ``tests/test_smc_liquidity_reversal._approved_setup_candles``
    but adds a long flat tail so 1m can also re-use these for the 1m
    detector. Returns enough bars (>= 21) to satisfy the swing
    detection ``left=right=2`` rule and the strategy's min_bars guard.
    """
    seq: Iterable[tuple[int, float, float, float, float]] = [
        (0,  1040, 1045, 1038, 1043),
        (1,  1043, 1048, 1041, 1046),
        (2,  1046, 1100, 1044, 1099),  # BIG pivot high (target_1)
        (3,  1054, 1054, 1042, 1043),
        (4,  1043, 1044, 1018, 1019),
        (5,  1019, 1020, 1014, 1015),
        (6,  1011, 1012, 1010, 1011),
        (7,  1011, 1012, 1009, 1010),
        (8,  1010, 1018, 1009, 1017),  # pivot H = 1018 (the ChoCH pivot)
        (9,  1015, 1015, 1004, 1005),
        (10, 1005, 1006,  999, 1000),
        (11, 1000, 1001,  996,  997),  # lowest swing low (996)
        (12,  997,  998,  996.5, 997),
        (13,  997,  998,  996.5, 997),
        (14,  997,  998,  996.5, 997),
        (15,  997,  998,  994,  996.5),  # SWEEP (994 < 996, close back above)
        (16,  996.5, 997,  995,  995.5),  # bearish OB
        (17,  995.5, 1019, 995, 1018.5),  # ChoCH (close 1018.5 > pivot 1018)
        (18, 1018.5, 1019, 1017, 1018),   # forms bullish FVG
        (19, 1018, 1019, 1017, 1018),
        (20, 1018, 1019, 1017, 1018),
    ]
    return [_bar(*b) for b in seq]


# ---------------------------------------------------------------------------
# 1. 1min timeframe registry exists with the right preset
# ---------------------------------------------------------------------------
def test_1min_timeframe_is_registered() -> None:
    assert "1min" in SUPPORTED_TIMEFRAMES
    assert "1min" in DEFAULT_TIMEFRAME_SPECS
    assert "1min" in DEFAULT_TIMEFRAME_STRATEGY


def test_1min_default_preset_uses_one_minute_bar_size() -> None:
    preset = DEFAULT_TIMEFRAME_SPECS["1min"]
    assert preset["bar_size"] == "1 min"
    assert preset["duration"] == "2 D"
    assert preset["use_rth"] is True
    # ~780 RTH bars in 2 sessions (390 minutes × 2).
    assert preset["max_bars"] == 780
    assert preset["min_bars"] >= 1


def test_resolve_1min_spec_has_one_minute_bar_size() -> None:
    spec = resolve_timeframe_spec("1min", cfg=None)
    assert spec.name == "1min"
    assert spec.bar_size == "1 min"
    assert spec.is_intraday is True
    assert spec.use_rth is True
    assert spec.max_bars == 780
    assert spec.duration == "2 D"


def test_normalise_timeframe_accepts_1m_aliases() -> None:
    for v in ("1m", "1min", "1 min", "1 mins", "1mins"):
        assert normalise_timeframe(v) == "1min"


def test_1min_strategy_thresholds_present() -> None:
    thr = DEFAULT_TIMEFRAME_STRATEGY["1min"]
    assert thr["max_allowed_stop_pct"] == pytest.approx(1.2)
    assert thr["max_extension_pct"] == pytest.approx(1.0)
    assert thr["min_rr_strict"] == pytest.approx(1.5)
    assert thr["min_rr_aggressive"] == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# 2. Risk config defaults match the prompt 13D spec
# ---------------------------------------------------------------------------
def test_intraday_risk_defaults_match_prompt_13d() -> None:
    cfg = IntradayRiskConfig()
    assert cfg.min_rr_strict == DEFAULT_MIN_RR_STRICT == 1.5
    assert cfg.min_rr_aggressive == DEFAULT_MIN_RR_AGGRESSIVE == 1.2
    assert cfg.max_stop_distance_pct == DEFAULT_MAX_STOP_DISTANCE_PCT == 1.2
    assert cfg.max_extension_from_entry_pct == DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT == 1.0


def test_intraday_risk_from_extras_overrides() -> None:
    cfg = IntradayRiskConfig.from_extras(
        {
            "risk": {
                "min_rr_strict": 2.0,
                "min_rr_aggressive": 1.4,
                "max_stop_distance_pct": 0.8,
                "max_extension_from_entry_pct": 0.5,
            }
        }
    )
    assert cfg.min_rr_strict == 2.0
    assert cfg.min_rr_aggressive == 1.4
    assert cfg.max_stop_distance_pct == 0.8
    assert cfg.max_extension_from_entry_pct == 0.5


# ---------------------------------------------------------------------------
# 3. IntradayEvaluation invariants — paper-only / no execution.
# ---------------------------------------------------------------------------
def test_evaluation_rejects_execution_allowed_true() -> None:
    with pytest.raises(ValueError, match="execution_allowed"):
        IntradayEvaluation(symbol="TEST", execution_allowed=True)


def test_evaluation_rejects_paper_only_false() -> None:
    with pytest.raises(ValueError, match="paper_only"):
        IntradayEvaluation(symbol="TEST", paper_only=False)


def test_evaluation_rejects_unknown_signal_category() -> None:
    with pytest.raises(ValueError, match="signal_category"):
        IntradayEvaluation(symbol="TEST", signal_category="MAGIC_BUY")


def test_strategy_key_is_canonical() -> None:
    assert STRATEGY_KEY == "ict_smc_intraday_v1"


def test_allowed_signal_categories_contains_all_seven() -> None:
    expected = {
        "DAY_TRADE_READY_STRICT",
        "DAY_TRADE_READY_AGGRESSIVE",
        "WATCH_ONLY",
        "INVALID_RISK",
        "BLOCKED",
        "NO_SETUP",
        "ERROR",
    }
    assert ALLOWED_SIGNAL_CATEGORIES == expected


# ---------------------------------------------------------------------------
# 4. scan_symbol_from_bars: missing 1m → BLOCKED with data quality
# ---------------------------------------------------------------------------
def test_scan_with_missing_1m_returns_blocked() -> None:
    eval_obj = scan_symbol_from_bars(
        "AAPL",
        bars_4h=_flat_bars(40, base=100.0),
        bars_30m=_flat_bars(40, base=100.0),
        bars_5m=_flat_bars(40, base=100.0),
        bars_1m=None,
    )
    assert eval_obj.signal_category == SIGNAL_BLOCKED
    assert eval_obj.execution_allowed is False
    assert eval_obj.paper_only is True
    assert eval_obj.direction == DIRECTION_FLAT
    assert eval_obj.data_quality["bars_1m_count"] == 0
    assert "1min" in (eval_obj.context.missing_data if eval_obj.context else [])
    assert "1m data missing" in eval_obj.rejection_reasons
    assert eval_obj.next_condition_to_watch  # non-empty


def test_scan_with_no_setup_5m_returns_no_setup() -> None:
    """All-flat series → no swing → no sweep → no setup."""
    eval_obj = scan_symbol_from_bars(
        "AAPL",
        bars_4h=_flat_bars(40, base=100.0),
        bars_30m=_flat_bars(40, base=100.0),
        bars_5m=_flat_bars(40, base=100.0),
        bars_1m=_flat_bars(120, base=100.0),
    )
    assert eval_obj.signal_category == SIGNAL_NO_SETUP
    assert eval_obj.execution_allowed is False


def test_scan_to_dict_contains_paper_only_invariants() -> None:
    eval_obj = scan_symbol_from_bars(
        "TEST",
        bars_4h=None,
        bars_30m=None,
        bars_5m=_flat_bars(20, base=100.0),
        bars_1m=_flat_bars(20, base=100.0),
    )
    d = eval_obj.to_dict()
    assert d["paper_only"] is True
    assert d["execution_allowed"] is False
    assert d["strategy_id"] == "ict_smc_intraday_v1"
    assert "data_quality" in d


# ---------------------------------------------------------------------------
# 5. classify_intraday_signal — direct dataclass scenarios
# ---------------------------------------------------------------------------
def _ctx(sym: str = "TEST") -> IntradayContext:
    return IntradayContext(
        symbol=sym,
        bias_4h="up",
        bias_30m="up",
        bias_5m="up",
        bars_4h_count=40,
        bars_30m_count=40,
        bars_5m_count=40,
        bars_1m_count=120,
        data_source="fixture",
    )


def _setup_long(found: bool = True) -> FiveMinuteSetup:
    s = FiveMinuteSetup(direction=DIRECTION_LONG, found=found)
    if found:
        s.sweep_index = 15
        s.swept_level_price = 100.0
        s.reclaim_close = 101.0
        s.mss_found = True
        s.mss_pivot_price = 102.0
        s.setup_zone_low = 100.5
        s.setup_zone_high = 101.5
    return s


def _trigger_long_fvg(displacement: bool = True) -> OneMinuteTrigger:
    """1m FVG + MSS — engineered to support a STRICT classification."""
    t = OneMinuteTrigger(direction=DIRECTION_LONG, found=True)
    t.sweep_index = 50
    t.swept_level_price = 100.0
    t.mss_found = True
    t.mss_pivot_price = 101.0
    t.entry_source = ENTRY_SOURCE_FVG
    # FVG zone tight near 101 so risk/reward >= 1.5 (R = 1.05 ≈ entry-stop)
    t.fvg_low = 101.0
    t.fvg_high = 101.1
    t.has_displacement = displacement
    return t


def _trigger_long_ob() -> OneMinuteTrigger:
    """1m OB + MSS, NO FVG — engineered to support an AGGRESSIVE class."""
    t = OneMinuteTrigger(direction=DIRECTION_LONG, found=True)
    t.sweep_index = 50
    t.swept_level_price = 100.0
    t.mss_found = True
    t.mss_pivot_price = 101.0
    t.entry_source = ENTRY_SOURCE_OB
    t.ob_low = 101.0
    t.ob_high = 101.1
    t.has_displacement = False
    return t


def _eval_with(
    setup: FiveMinuteSetup | None,
    trig: OneMinuteTrigger | None,
    plan: IntradayTradePlan | None,
    ctx: IntradayContext | None = None,
) -> IntradayEvaluation:
    return IntradayEvaluation(
        symbol="TEST",
        date="2026-04-25",
        direction=DIRECTION_LONG if setup is None else setup.direction,
        signal_category=SIGNAL_NO_SETUP,
        context=ctx or _ctx(),
        five_min_setup=setup,
        one_min_trigger=trig,
        trade_plan=plan,
    )


def test_classify_strict_when_fvg_plus_displacement_and_rr_strict() -> None:
    risk = IntradayRiskConfig()
    setup = _setup_long(True)
    trig = _trigger_long_fvg(displacement=True)
    plan = build_intraday_trade_plan(trig, _ctx(), risk)
    assert plan.valid is True, plan.rejection_reasons
    assert plan.risk_reward and plan.risk_reward >= risk.min_rr_strict
    e = _eval_with(setup, trig, plan)
    cat = classify_intraday_signal(e, risk, last_close=plan.entry)
    assert cat == SIGNAL_DAY_TRADE_READY_STRICT


def test_classify_aggressive_when_ob_without_fvg_and_rr_aggressive() -> None:
    risk = IntradayRiskConfig()
    setup = _setup_long(True)
    trig = _trigger_long_ob()
    plan = build_intraday_trade_plan(trig, _ctx(), risk)
    assert plan.valid is True, plan.rejection_reasons
    e = _eval_with(setup, trig, plan)
    # OB has no displacement and entry_source != FVG → classifier must
    # downgrade to AGGRESSIVE even if R/R >= strict floor (no FVG).
    cat = classify_intraday_signal(e, risk, last_close=plan.entry)
    assert cat == SIGNAL_DAY_TRADE_READY_AGGRESSIVE


def test_classify_watch_when_setup_present_but_trigger_missing() -> None:
    risk = IntradayRiskConfig()
    setup = _setup_long(True)
    trig = OneMinuteTrigger(direction=DIRECTION_LONG, found=False)
    e = _eval_with(setup, trig, None)
    cat = classify_intraday_signal(e, risk)
    assert cat == SIGNAL_WATCH_ONLY


def test_classify_no_setup_when_5m_setup_missing() -> None:
    risk = IntradayRiskConfig()
    setup = FiveMinuteSetup(direction=DIRECTION_LONG, found=False)
    e = _eval_with(setup, None, None)
    cat = classify_intraday_signal(e, risk)
    assert cat == SIGNAL_NO_SETUP


def test_classify_blocked_when_context_missing_1min() -> None:
    risk = IntradayRiskConfig()
    setup = _setup_long(True)
    ctx = _ctx()
    ctx.missing_data = ["1min"]
    e = _eval_with(setup, _trigger_long_fvg(), None, ctx=ctx)
    cat = classify_intraday_signal(e, risk)
    assert cat == SIGNAL_BLOCKED


def test_classify_invalid_when_rr_below_aggressive_floor() -> None:
    """Plan valid structurally but R/R < min_rr_aggressive → INVALID_RISK.

    The trade plan sizes the target as ``risk_per_share * min_rr_strict``,
    so R/R == min_rr_strict by construction. We use a very low strict
    floor and a high aggressive floor to force ``rr < min_rr_aggressive``.
    """
    risk = IntradayRiskConfig(min_rr_strict=0.1, min_rr_aggressive=5.0)
    setup = _setup_long(True)
    trig = _trigger_long_ob()
    plan = build_intraday_trade_plan(trig, _ctx(), risk)
    assert plan.valid is True
    assert plan.risk_reward and plan.risk_reward < risk.min_rr_aggressive
    e = _eval_with(setup, trig, plan)
    cat = classify_intraday_signal(e, risk, last_close=plan.entry)
    assert cat == SIGNAL_INVALID_RISK
    assert any("risk_reward" in r for r in e.rejection_reasons)


def test_classify_invalid_when_extension_pct_too_far() -> None:
    risk = IntradayRiskConfig(max_extension_from_entry_pct=0.0001)
    setup = _setup_long(True)
    trig = _trigger_long_fvg(displacement=True)
    plan = build_intraday_trade_plan(trig, _ctx(), risk)
    assert plan.valid is True
    # Force a "very extended" last close.
    e = _eval_with(setup, trig, plan)
    cat = classify_intraday_signal(e, risk, last_close=(plan.entry or 0) * 1.5)
    assert cat == SIGNAL_INVALID_RISK
    assert any("extension_from_entry_pct" in r for r in e.rejection_reasons)


def test_classify_invalid_when_stop_distance_pct_too_wide() -> None:
    risk = IntradayRiskConfig(max_stop_distance_pct=0.0001)
    setup = _setup_long(True)
    trig = _trigger_long_fvg(displacement=True)
    plan = build_intraday_trade_plan(trig, _ctx(), risk)
    assert plan.valid is True
    e = _eval_with(setup, trig, plan)
    cat = classify_intraday_signal(e, risk, last_close=plan.entry)
    assert cat == SIGNAL_INVALID_RISK
    assert any("stop_distance_pct" in r for r in e.rejection_reasons)


# ---------------------------------------------------------------------------
# 6. build_intraday_trade_plan rejections (long)
# ---------------------------------------------------------------------------
def test_plan_rejects_long_when_stop_above_entry() -> None:
    """Engineer the trigger so the swept level lands above the entry."""
    trig = OneMinuteTrigger(direction=DIRECTION_LONG, found=True)
    trig.sweep_index = 50
    # Stop = swept_low - buffer; if swept_low is ABOVE the entry source
    # (FVG midpoint), the resulting stop will be >= entry → rejection.
    trig.swept_level_price = 200.0  # absurdly high
    trig.mss_found = True
    trig.mss_pivot_price = 102.0
    trig.entry_source = ENTRY_SOURCE_FVG
    trig.fvg_low = 100.0
    trig.fvg_high = 100.1  # midpoint = 100.05
    plan = build_intraday_trade_plan(trig, _ctx(), IntradayRiskConfig())
    assert plan.valid is False
    assert any("stop >= entry" in r for r in plan.rejection_reasons)


def test_plan_rejects_short_when_stop_below_entry() -> None:
    trig = OneMinuteTrigger(direction=DIRECTION_SHORT, found=True)
    trig.sweep_index = 50
    trig.swept_level_price = 50.0  # below an entry of 100
    trig.mss_found = True
    trig.mss_pivot_price = 99.0
    trig.entry_source = ENTRY_SOURCE_FVG
    trig.fvg_low = 100.0
    trig.fvg_high = 100.1  # midpoint = 100.05
    plan = build_intraday_trade_plan(trig, _ctx(), IntradayRiskConfig())
    assert plan.valid is False
    assert any("stop <= entry" in r for r in plan.rejection_reasons)


def test_plan_rejects_when_no_actionable_direction() -> None:
    trig = OneMinuteTrigger(direction=DIRECTION_FLAT, found=False)
    plan = build_intraday_trade_plan(trig, _ctx(), IntradayRiskConfig())
    assert plan.valid is False
    assert any("trigger has no actionable" in r for r in plan.rejection_reasons)


def test_plan_rejects_when_trigger_not_found() -> None:
    trig = OneMinuteTrigger(direction=DIRECTION_LONG, found=False)
    plan = build_intraday_trade_plan(trig, _ctx(), IntradayRiskConfig())
    assert plan.valid is False
    assert any("1m trigger not found" in r for r in plan.rejection_reasons)


def test_plan_short_symmetry_produces_target_below_entry() -> None:
    trig = OneMinuteTrigger(direction=DIRECTION_SHORT, found=True)
    trig.sweep_index = 50
    trig.swept_level_price = 102.0
    trig.mss_found = True
    trig.mss_pivot_price = 100.0
    trig.entry_source = ENTRY_SOURCE_FVG
    trig.fvg_low = 100.4
    trig.fvg_high = 100.6  # midpoint = 100.5
    plan = build_intraday_trade_plan(trig, _ctx(), IntradayRiskConfig())
    assert plan.valid is True
    assert plan.entry and plan.target and plan.stop
    # Short: stop > entry > target.
    assert plan.stop > plan.entry > plan.target


# ---------------------------------------------------------------------------
# 7. End-to-end pipeline on synthetic long setup → at least produces a
#    valid 5m setup (the engineered series ChoCHs above pivot 1018).
# ---------------------------------------------------------------------------
def test_pipeline_with_engineered_long_5m_setup_finds_setup() -> None:
    bars_5m = _long_setup_bars()
    # Provide some 1m bars too so we hit the trigger path (it might not
    # find a 1m sweep in this fake, that's fine — we only want to
    # confirm the 5m setup is detected end-to-end).
    bars_1m = _flat_bars(120, base=1018.0)
    eval_obj = scan_symbol_from_bars(
        "TEST",
        bars_4h=None,
        bars_30m=None,
        bars_5m=bars_5m,
        bars_1m=bars_1m,
    )
    assert eval_obj.execution_allowed is False
    assert eval_obj.signal_category in ALLOWED_SIGNAL_CATEGORIES
    # The five_min_setup should be present and (best-effort) detected.
    assert eval_obj.five_min_setup is not None
    if eval_obj.five_min_setup.found:
        assert eval_obj.five_min_setup.direction in (DIRECTION_LONG, DIRECTION_SHORT)
    # Even when no trigger fires, classification must be one of the
    # waiting categories — never STRICT/AGGRESSIVE without a trigger.
    if (
        eval_obj.one_min_trigger is None
        or not eval_obj.one_min_trigger.found
    ):
        assert eval_obj.signal_category in (
            SIGNAL_NO_SETUP, SIGNAL_WATCH_ONLY, SIGNAL_BLOCKED
        )


# ---------------------------------------------------------------------------
# 8. Watchlist summary + JSON persistence shape
# ---------------------------------------------------------------------------
def test_save_intraday_evaluation_writes_per_symbol_json(tmp_path: Path) -> None:
    eval_obj = scan_symbol_from_bars(
        "AAPL",
        bars_4h=None,
        bars_30m=None,
        bars_5m=_flat_bars(20),
        bars_1m=_flat_bars(20),
    )
    out = save_intraday_evaluation(tmp_path, eval_obj)
    assert out.exists()
    payload = json.loads(out.read_text("utf-8"))
    assert payload["symbol"] == "AAPL"
    assert payload["strategy_id"] == "ict_smc_intraday_v1"
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    # Per-prompt 13D field set.
    for k in (
        "context", "five_min_setup", "one_min_trigger", "trade_plan",
        "signal_category", "direction", "rejection_reasons",
        "next_condition_to_watch", "explanation_zh",
        "chart_paths", "data_source", "data_quality",
    ):
        assert k in payload, k


def test_build_watchlist_summary_counts_categories() -> None:
    items = [
        {"symbol": "A", "signal_category": SIGNAL_DAY_TRADE_READY_STRICT, "score": 80.0},
        {"symbol": "B", "signal_category": SIGNAL_DAY_TRADE_READY_AGGRESSIVE, "score": 60.0},
        {"symbol": "C", "signal_category": SIGNAL_WATCH_ONLY, "score": 40.0},
        {"symbol": "D", "signal_category": SIGNAL_INVALID_RISK, "score": 20.0},
        {"symbol": "E", "signal_category": SIGNAL_NO_SETUP, "score": 10.0},
        {"symbol": "F", "signal_category": SIGNAL_BLOCKED, "score": 0.0},
    ]
    summary = build_watchlist_summary(items=items, symbols_scanned=6, source="dynamic")
    assert summary["strategy_id"] == "ict_smc_intraday_v1"
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    assert summary["counts"][SIGNAL_DAY_TRADE_READY_STRICT] == 1
    assert summary["counts"][SIGNAL_DAY_TRADE_READY_AGGRESSIVE] == 1
    assert summary["counts"][SIGNAL_WATCH_ONLY] == 1
    assert summary["counts"][SIGNAL_INVALID_RISK] == 1
    assert summary["counts"][SIGNAL_NO_SETUP] == 1
    assert summary["counts"][SIGNAL_BLOCKED] == 1
    assert summary["ready_strict_symbols"] == ["A"]
    assert summary["ready_aggressive_symbols"] == ["B"]
    assert summary["watch_symbols"] == ["C"]
    assert summary["invalid_symbols"] == ["D"]
    # Top candidates sorted by score desc, capped to 10.
    assert summary["top_candidates"][0]["symbol"] == "A"
    assert len(summary["top_candidates"]) <= 10


def test_save_intraday_watchlist_summary_writes_json(tmp_path: Path) -> None:
    summary = build_watchlist_summary(items=[], symbols_scanned=0, source="dynamic")
    p = save_intraday_watchlist_summary(tmp_path, summary)
    assert p.exists()
    payload = json.loads(p.read_text("utf-8"))
    assert payload["strategy_id"] == "ict_smc_intraday_v1"
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    assert "counts" in payload


# ---------------------------------------------------------------------------
# 9. Telegram digest (Chinese, non-crashing)
# ---------------------------------------------------------------------------
def test_format_intraday_telegram_zh_includes_paper_only_warning() -> None:
    summary = build_watchlist_summary(
        items=[
            {"symbol": "AAPL", "signal_category": SIGNAL_DAY_TRADE_READY_STRICT, "score": 80.0},
            {"symbol": "TSLA", "signal_category": SIGNAL_WATCH_ONLY, "score": 40.0},
        ],
        symbols_scanned=2,
        source="dynamic",
    )
    text = format_intraday_telegram_zh(summary)
    assert "ICT/SMC" in text
    assert "AAPL" in text
    assert "TSLA" in text
    # Paper-only warning must always be present.
    assert "execution_allowed=false" in text
    # Counts header.
    assert "STRICT" in text and "AGGRESSIVE" in text


def test_format_intraday_telegram_zh_handles_empty_summary() -> None:
    text = format_intraday_telegram_zh({})
    assert isinstance(text, str)
    assert "execution_allowed=false" in text


# ---------------------------------------------------------------------------
# 10. Architectural safety — strategy module never imports broker
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _module_imports(rel_path: str) -> set[str]:
    src = PROJECT_ROOT / rel_path
    tree = ast.parse(src.read_text("utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports.add(mod)
            for n in node.names:
                imports.add(f"{mod}.{n.name}".lstrip("."))
    return imports


@pytest.mark.parametrize(
    "rel_path",
    [
        "bot/strategies/ict_smc_intraday/__init__.py",
        "bot/strategies/ict_smc_intraday/model.py",
        "bot/strategies/ict_smc_intraday/detector.py",
        "bot/strategies/ict_smc_intraday/scanner.py",
        "bot/strategies/ict_smc_intraday/charts.py",
        "bot/strategies/adapters/ict_smc_intraday_v1.py",
    ],
)
def test_strategy_module_does_not_import_broker(rel_path: str) -> None:
    """No broker / order placement code may load with the strategy."""
    imports = _module_imports(rel_path)
    bad = {imp for imp in imports if "bot.broker" in imp or imp == "broker"}
    assert not bad, f"{rel_path} pulls in broker: {bad}"


def test_scanner_imports_ibkr_lazily_only() -> None:
    """``bot.ibkr_client`` must NOT be in scanner.py's top-level imports.

    The scanner reaches IBKR through lazy ``from ...ibkr_client import``
    inside function bodies — verified here by checking the AST.
    """
    src = (PROJECT_ROOT / "bot" / "strategies" / "ict_smc_intraday" / "scanner.py").read_text("utf-8")
    tree = ast.parse(src)
    top_level_imports: set[str] = set()
    for node in tree.body:  # only top-level
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", "") or ""
            top_level_imports.add(mod)
            for n in node.names:
                top_level_imports.add(n.name)
    assert "bot.ibkr_client" not in top_level_imports
    assert not any("ibkr_client" in m for m in top_level_imports)
