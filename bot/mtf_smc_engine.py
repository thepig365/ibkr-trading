"""Multi-timeframe SMC/ICT pattern recognition (Prompt 10B).

Research-only. No broker imports. Outputs JSON-friendly dicts; execution
stays disabled globally. ``eligible_for_future_paper_trade`` is a flag
for human/forward review only, never an order signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from .config import AppConfig
from .market_structure import (
    Candles,
    candles_from_records,
    detect_choch_after_sweep,
    detect_liquidity_sweep,
    detect_swing_highs,
    detect_swing_lows,
)
from .smc_timeframes import normalise_timeframe, resolve_strategy_thresholds
from .strategy_engine import (
    DEFAULT_STRATEGY_CFG,
    STRATEGY_NAME,
    StrategyEvaluation,
    evaluate_smc_liquidity_reversal,
)

# --------------------------------------------------------------------------- #
#Types
# --------------------------------------------------------------------------- #

AlignmentCategory = Literal[
    "FULL_ALIGNMENT",
    "SETUP_READY_WAITING_TRIGGER",
    "BIAS_OK_SETUP_INCOMPLETE",
    "CONFLICTED",
    "BLOCKED",
]

DailyBias = Literal["bullish", "bearish", "neutral", "unknown"]
Structure4H = Literal[
    "bullish_confirmed",
    "bearish_confirmed",
    "range",
    "transitional",
    "unknown",
]
Setup30m = Literal[
    "full_setup_valid",
    "waiting_for_pullback",
    "too_extended",
    "invalid_risk",
    "incomplete",
    "blocked",
    "unknown",
]
Trigger5m = Literal[
    "confirmed",
    "waiting_for_pullback",
    "waiting_for_choch",
    "invalid",
    "unknown",
]
PremiumZone = Literal["discount", "equilibrium", "premium", "unknown"]


# --------------------------------------------------------------------------- #
# Math helpers
# --------------------------------------------------------------------------- #


def _sma(closes: Sequence[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    s = [float(c) for c in closes if math.isfinite(float(c))]
    if len(s) < n:
        return None
    w = s[-n:]
    return sum(w) / float(n)


def _num(x: object) -> float | None:
    if isinstance(x, (int, float)) and math.isfinite(float(x)):
        return float(x)
    return None


# --------------------------------------------------------------------------- #
# Part B — daily bias
# --------------------------------------------------------------------------- #


def classify_daily_bias(
    daily_rows: list[dict[str, Any]],
    *,
    market_regime: str = "neutral",
) -> dict[str, Any]:
    """Deterministic V1 daily bias (long-side lens)."""
    if not daily_rows or len(daily_rows) < 20:
        return {
            "bias": "unknown",
            "reason": "insufficient daily history",
            "evidence": {"bars": len(daily_rows)},
        }
    try:
        c = candles_from_records(daily_rows)
    except Exception as exc:  # noqa: BLE001
        return {
            "bias": "unknown",
            "reason": f"candle parse: {exc}",
            "evidence": {},
        }
    close = c[-1].close
    closes = [b.close for b in c]
    ma200 = _sma(closes, 200)
    ma20 = _sma(closes, 20)

    sh = detect_swing_highs(c, left=2, right=2, allow_unconfirmed=False)
    sl = detect_swing_lows(c, left=2, right=2, allow_unconfirmed=False)
    hh = (
        sh[-1].price > sh[-2].price
        if len(sh) >= 2
        else None
    )
    hl = (
        sl[-1].price > sl[-2].price
        if len(sl) >= 2
        else None
    )
    lh = (
        sh[-1].price < sh[-2].price
        if len(sh) >= 2
        else None
    )
    ll = (
        sl[-1].price < sl[-2].price
        if len(sl) >= 2
        else None
    )

    sweeps = detect_liquidity_sweep(c, lookback=20, require_close_back_above=True)
    sweep = sweeps[-1] if sweeps else None
    choch = None
    if sweep:
        choch = detect_choch_after_sweep(
            c,
            sweep,
            max_bars_after_sweep=10,
            require_close_above_pivot_high=True,
        )

    struct_bull = hh is True and hl is True
    struct_bear = lh is True and ll is True

    evidence: dict[str, Any] = {
        "last_close": close,
        "sma_200": ma200,
        "sma_20": ma20,
        "swing_deltas": {
            "higher_highs": hh,
            "higher_lows": hl,
            "lower_highs": lh,
            "lower_lows": ll,
        },
        "liquidity_sweep_found": bool(sweeps),
        "choch_found": bool(choch and choch.get("found")),
    }

    # Bearish: close below 200 and clear bearish structure
    if (
        ma200 is not None
        and close < ma200
        and struct_bear
    ):
        return {
            "bias": "bearish",
            "reason": "close below 200SMA and lower high / lower low structure",
            "evidence": evidence,
        }

    # Regime (macro) bearish
    if market_regime in ("risk_off", "crisis"):
        return {
            "bias": "bearish",
            "reason": f"market_regime={market_regime!r} weights bearish",
            "evidence": {**evidence, "market_regime": market_regime},
        }

    # Bullish: reclaim / ChoCH
    if sweep and choch and choch.get("found") and not struct_bear:
        if close > (ma200 or close):
            return {
                "bias": "bullish",
                "reason": "sweep + bullish ChoCH with price above/through key MA context",
                "evidence": evidence,
            }

    if ma200 is not None and close > ma200 and not struct_bear:
        if struct_bull or (hh is not False and hl is not False):
            return {
                "bias": "bullish",
                "reason": "close above 200SMA; structure not clearly bearish",
                "evidence": evidence,
            }

    if (ma200 is None or abs(close - ma200) / max(ma200, 1e-9) < 0.01) and ma20:
        return {
            "bias": "neutral",
            "reason": "price near 200SMA or mixed swing evidence (range / equilibrium)",
            "evidence": evidence,
        }

    if struct_bull and (ma200 and close < ma200):
        return {
            "bias": "neutral",
            "reason": "mixed: recovery structure but still below 200SMA",
            "evidence": evidence,
        }

    return {
        "bias": "neutral",
        "reason": "insufficient agreement between MA and swing structure",
        "evidence": evidence,
    }


# --------------------------------------------------------------------------- #
# Part C — 4H structure
# --------------------------------------------------------------------------- #


def classify_4h_structure(
    rows_4h: list[dict[str, Any]],
    *,
    eval4h: StrategyEvaluation,
) -> dict[str, Any]:
    """Classify 4H structure from SMC evaluation + heuristics."""
    if not rows_4h or len(rows_4h) < 20:
        return {
            "structure": "unknown",
            "reason": "insufficient 4h data",
            "evidence": {"bars": len(rows_4h)},
        }
    c = candles_from_records(rows_4h)
    sweeps = detect_liquidity_sweep(
        c, lookback=20, require_close_back_above=True
    )
    sweep = sweeps[-1] if sweeps else None
    choch = None
    if sweep:
        choch = detect_choch_after_sweep(
            c, sweep, max_bars_after_sweep=10, require_close_above_pivot_high=True
        )
    ev = eval4h
    seq = ev.sequence or {}
    approved = ev.approved_for_dry_run
    evidence = {
        "sweep_4h": bool(sweeps),
        "choch_4h": bool(choch and choch.get("found")),
        "smc_approved_4h": approved,
    }

    if approved:
        return {
            "structure": "bullish_confirmed",
            "reason": "4H SMC liquidity-reversal plan approved in research sense",
            "evidence": evidence,
        }
    if sweep and choch and choch.get("found"):
        return {
            "structure": "bullish_confirmed",
            "reason": "4H sell-side sweep + ChoCH (bullish shift)",
            "evidence": evidence,
        }
    if sweep and not (choch and choch.get("found")):
        return {
            "structure": "transitional",
            "reason": "sweep on 4H but ChoCH not complete",
            "evidence": evidence,
        }
    if bool((seq.get("sweep") or {}).get("found")) and not bool(
        (seq.get("choch") or {}).get("found")
    ):
        return {
            "structure": "transitional",
            "reason": "SMC engine sees partial sequence on 4H",
            "evidence": evidence,
        }

    sh = detect_swing_highs(c, left=2, right=2, allow_unconfirmed=True)
    sl = detect_swing_lows(c, left=2, right=2, allow_unconfirmed=True)
    if len(sh) >= 3 and len(sl) >= 3:
        w = 8
        recent = c[-w:]
        hi = max(x.high for x in recent)
        lo = min(x.low for x in recent)
        if hi - lo > 0 and (hi - lo) / max(abs(c[-1].close), 1e-9) < 0.02:
            return {
                "structure": "range",
                "reason": "tight oscillation, no clean ChoCH / BOS follow-through",
                "evidence": evidence,
            }

    rejs = " ".join(ev.rejection_reasons or [])
    if rejs and "bear" in rejs.lower():
        return {
            "structure": "bearish_confirmed",
            "reason": "4H path skews bearish per SMC risk / sequence rejection heuristics",
            "evidence": {**evidence, "rejection_blurb": rejs[:180]},
        }

    return {
        "structure": "unknown",
        "reason": "4H: inconclusive (see SMC rejection reasons)",
        "evidence": {**evidence, "rejection_reasons": list(ev.rejection_reasons or [])},
    }


# --------------------------------------------------------------------------- #
# Part D — 30m setup mapping
# --------------------------------------------------------------------------- #


def map_setup_30min(
    ev: StrategyEvaluation,
    *,
    market_regime: str,
) -> dict[str, Any]:
    """Map :class:`StrategyEvaluation` to ``setup_30min`` state."""
    th = (ev.notes and any("extended" in n.lower() for n in ev.notes)) or False
    plan = ev.trade_plan or {}
    sp = _num(plan.get("stop_distance_pct"))
    ex = _num(plan.get("extension_pct_vs_latest_close"))
    rr = _num(plan.get("risk_reward_to_target_1"))
    entry = _num(plan.get("entry_price"))
    stop = _num(plan.get("structural_stop"))
    t1 = _num(plan.get("target_1"))
    min_rr = 1.8
    max_ext = 1.0
    max_stop = 2.0
    rej = " ".join(ev.rejection_reasons or [])
    if "halt" in rej.lower() or "blocked_by_news" in rej.lower():
        state: Setup30m = "blocked"
    elif market_regime in ("risk_off", "crisis", "unknown") and (
        "market_regime" in rej.lower() or "blocks" in rej.lower()
    ):
        state = "blocked"
    else:
        seq = ev.sequence or {}
        full_struct = all(
            bool((seq.get(k) or {}).get("found"))
            for k in ("sweep", "choch", "fvg", "order_block")
        )
        if not full_struct:
            state = "incomplete"
        elif (
            (sp is not None and sp > max_stop)
            or (rr is not None and rr < min_rr)
            or not t1
        ):
            state = "invalid_risk"
        elif (ex is not None and ex > max_ext) or th:
            if ex and ex > max_ext * 1.2:
                state = "too_extended"
            else:
                state = "waiting_for_pullback"
        elif ev.approved_for_dry_run and full_struct:
            state = "full_setup_valid"
        else:
            state = "unknown"
    return {
        "setup_state": state,
        "entry_price": entry,
        "stop_price": stop,
        "target_1": t1,
        "risk_reward": rr,
        "stop_distance_pct": sp,
        "extension_pct_vs_latest_close": ex,
        "reason": (ev.rejection_reasons or [""])[-1] if not ev.approved_for_dry_run else "",
        "source_evaluation": {
            "approved_for_dry_run": ev.approved_for_dry_run,
            "rejection_reasons": list(ev.rejection_reasons or []),
        },
    }


# --------------------------------------------------------------------------- #
# Part E — 5m trigger
# --------------------------------------------------------------------------- #


def _zone_touch(
    c5: Candles, low: float, high: float
) -> bool:
    for x in c5[-30:]:
        if x.high >= low and x.low <= high:
            return True
    return False


def _displacement_up(c5: Candles) -> bool:
    if len(c5) < 3:
        return False
    a, b, d = c5[-3], c5[-2], c5[-1]
    rng = max(a.high - a.low, 1e-9)
    return d.close > a.high and (d.close - a.open) > 0.4 * rng


def classify_5min_trigger(
    ev5: StrategyEvaluation,
    candles_5: Candles,
    *,
    setup_30: Mapping[str, Any],
    th5: Mapping[str, Any],
) -> dict[str, Any]:
    st = str(setup_30.get("setup_state") or "unknown")
    if st in ("blocked", "invalid_risk"):
        return {
            "trigger_state": "invalid",
            "reason": "30min setup not valid for trigger alignment",
            "evidence": {"setup_30": st},
            "trigger_entry_price": None,
            "trigger_stop_price": None,
        }
    if st in ("incomplete", "unknown") and not setup_30.get("entry_price"):
        return {
            "trigger_state": "unknown",
            "reason": "30min setup not valid (no entry zone)",
            "evidence": {"setup_30": st},
            "trigger_entry_price": None,
            "trigger_stop_price": None,
        }
    entry_30 = _num(setup_30.get("entry_price"))
    if entry_30 is None or st in ("incomplete", "blocked", "invalid_risk", "unknown"):
        return {
            "trigger_state": "unknown",
            "reason": "30min setup not valid",
            "evidence": {},
            "trigger_entry_price": None,
            "trigger_stop_price": None,
        }
    last_close = candles_5[-1].close
    tol = float(th5.get("trigger_entry_tolerance_pct", 0.5) or 0.5) / 100.0
    band = entry_30 * tol
    in_zone = abs(last_close - entry_30) <= band
    fvg5 = (ev5.sequence or {}).get("fvg") or {}
    ch5 = (ev5.sequence or {}).get("choch") or {}
    sw5 = (ev5.sequence or {}).get("sweep") or {}
    plan5 = ev5.trade_plan or {}
    tstop = _num(plan5.get("structural_stop"))
    max_tr_ext = float(th5.get("max_trigger_extension_pct", 0.5) or 0.5)
    ex5 = _num(plan5.get("extension_pct_vs_latest_close")) or 0.0
    req_fvg = bool(th5.get("require_5min_fvg_or_displacement", True))
    fvg_or_disp = (fvg5.get("found") or _displacement_up(candles_5))

    if not in_zone and not _zone_touch(
        candles_5, entry_30 - 2 * band, entry_30 + 2 * band
    ):
        return {
            "trigger_state": "waiting_for_pullback",
            "reason": "5m price not in/near 30m entry zone (tolerance and touch)",
            "evidence": {"last_5m_close": last_close, "entry_30": entry_30},
            "trigger_entry_price": _num(plan5.get("entry_price")),
            "trigger_stop_price": tstop,
        }

    if in_zone or _zone_touch(
        candles_5, entry_30 - 2 * band, entry_30 + 2 * band
    ):
        if ex5 > max_tr_ext:
            return {
                "trigger_state": "invalid",
                "reason": f"5m extension {ex5:.2f}% over trigger cap {max_tr_ext:.2f}%",
                "evidence": {"extension_5m_pct": ex5},
                "trigger_entry_price": _num(plan5.get("entry_price")),
                "trigger_stop_price": tstop,
            }
        if not (sw5.get("found") and ch5.get("found")):
            return {
                "trigger_state": "waiting_for_choch",
                "reason": "in 30m entry zone on 5m but no 5m sweep+ChoCH yet",
                "evidence": {"sweep_5m": bool(sw5.get("found")), "choch_5m": bool(ch5.get("found"))},
                "trigger_entry_price": _num(plan5.get("entry_price")),
                "trigger_stop_price": tstop,
            }
        if req_fvg and not fvg_or_disp:
            return {
                "trigger_state": "waiting_for_choch",
                "reason": "need 5m FVG or displacement for confirmation (config)",
                "evidence": {},
                "trigger_entry_price": _num(plan5.get("entry_price")),
                "trigger_stop_price": tstop,
            }
        if ev5.approved_for_dry_run or (
            sw5.get("found") and ch5.get("found") and fvg_or_disp
        ):
            return {
                "trigger_state": "confirmed",
                "reason": "5m sweep+ChoCH with FVG/displacement; structural stop available",
                "evidence": {
                    "fvg_5m": bool(fvg5.get("found")),
                    "displacement": _displacement_up(candles_5),
                },
                "trigger_entry_price": _num(plan5.get("entry_price")),
                "trigger_stop_price": tstop,
            }

    return {
        "trigger_state": "unknown",
        "reason": "5m trigger inconclusive",
        "evidence": {},
        "trigger_entry_price": _num(plan5.get("entry_price")),
        "trigger_stop_price": tstop,
    }


# --------------------------------------------------------------------------- #
# Part F — premium / discount
# --------------------------------------------------------------------------- #


def compute_premium_discount(
    *,
    rows_4h: list[dict[str, Any]],
    rows_30: list[dict[str, Any]],
    latest_price: float,
) -> dict[str, Any]:
    """Use 4H swing range when available; else 30m."""
    c4 = None
    if rows_4h and len(rows_4h) >= 4:
        try:
            c4 = candles_from_records(rows_4h)
        except Exception:  # noqa: BLE001
            c4 = None
    c3 = None
    if not c4 and rows_30 and len(rows_30) >= 4:
        try:
            c3 = candles_from_records(rows_30)
        except Exception:  # noqa: BLE001
            c3 = None
    source = c4 or c3
    tf = "4h" if c4 is not None else "30min" if c3 is not None else "unknown"
    if source is None or len(source) < 2:
        return {
            "timeframe": tf,
            "range_low": None,
            "range_high": None,
            "midpoint": None,
            "latest_price": latest_price,
            "current_zone": "unknown",
        }
    w = min(80, len(source))
    seg = source[-w:]
    lo = min(x.low for x in seg)
    hi = max(x.high for x in seg)
    mid = (lo + hi) / 2.0
    if not math.isfinite(latest_price) or mid <= 0:
        return {
            "timeframe": tf,
            "range_low": lo,
            "range_high": hi,
            "midpoint": mid,
            "latest_price": latest_price,
            "current_zone": "unknown",
        }
    if latest_price < mid * 0.995:
        zone: PremiumZone = "discount"
    elif latest_price > mid * 1.005:
        zone = "premium"
    else:
        zone = "equilibrium"
    return {
        "timeframe": tf,
        "range_low": lo,
        "range_high": hi,
        "midpoint": mid,
        "latest_price": latest_price,
        "current_zone": zone,
    }


# --------------------------------------------------------------------------- #
# Part G — score + alignment
# --------------------------------------------------------------------------- #


def _pd_adjust(zone: str) -> int:
    if zone == "discount":
        return 5
    if zone == "equilibrium":
        return 2
    if zone == "premium":
        return -10
    return 0


def _daily_pts(b: str) -> int:
    return {"bullish": 20, "neutral": 10, "bearish": -30, "unknown": 0}.get(
        b, 0
    )


def _4h_pts(s: str) -> int:
    return {
        "bullish_confirmed": 25,
        "transitional": 10,
        "range": 5,
        "bearish_confirmed": -30,
        "unknown": 0,
    }.get(s, 0)


def _30_pts(st: str) -> int:
    if st == "full_setup_valid":
        return 30
    if st in ("waiting_for_pullback", "too_extended"):
        return 10
    if st == "incomplete":
        return 5
    if st == "invalid_risk":
        return -20
    if st == "blocked":
        return -30
    return 0


def _5_pts(t: str) -> int:
    return {
        "confirmed": 20,
        "waiting_for_pullback": 5,
        "waiting_for_choch": 5,
        "invalid": -20,
        "unknown": 0,
    }.get(t, 0)


def resolve_alignment(
    *,
    daily_bias: str,
    s4: str,
    s30: str,
    t5: str,
    premium_zone: str,
    mtf_score: int,
) -> tuple[AlignmentCategory, list[str], bool, list[str]]:
    """Return (category, not_eligible_reasons, eligible_research_flag, not_elig_long_list)."""
    not_elig: list[str] = []
    d_bull = daily_bias in ("bullish", "neutral")
    c_bull_4h = s4 in ("bullish_confirmed", "transitional", "range")
    conflict = (daily_bias == "bullish" and s4 == "bearish_confirmed") or (
        daily_bias == "bearish" and s30 in ("full_setup_valid", "waiting_for_pullback")
    )
    if conflict or (daily_bias == "bearish" and s4 == "bearish_confirmed" and s30 == "full_setup_valid"):
        return (
            "CONFLICTED",
            ["timeframe direction conflict"],
            False,
            ["conflicted_mtf"],
        )
    if s30 in ("blocked",) or t5 in ("invalid",):
        not_elig.append("blocked_or_invalid_mtf")
        return ("BLOCKED", not_elig, False, not_elig)
    if s30 == "invalid_risk":
        not_elig.append("invalid_risk")
        return ("BLOCKED", not_elig, False, not_elig)
    if premium_zone == "premium" and s30 == "full_setup_valid":
        not_elig.append("premium_context_penalty")

    if (
        d_bull
        and c_bull_4h
        and s30 == "full_setup_valid"
        and t5 == "confirmed"
        and premium_zone != "premium"
        and mtf_score >= 75
    ):
        return (
            "FULL_ALIGNMENT",
            [],
            True,
            [],
        )
    if (
        d_bull
        and c_bull_4h
        and s30 in ("full_setup_valid", "waiting_for_pullback", "too_extended")
        and t5 in ("waiting_for_pullback", "waiting_for_choch")
    ):
        return (
            "SETUP_READY_WAITING_TRIGGER",
            not_elig,
            False,
            not_elig or ["awaiting_5m_trigger"],
        )
    if d_bull and c_bull_4h and s30 == "incomplete":
        return (
            "BIAS_OK_SETUP_INCOMPLETE",
            not_elig,
            False,
            not_elig or ["30m_structure_incomplete"],
        )
    if not d_bull or s4 in ("unknown",) or t5 in ("invalid",):
        return (
            "BLOCKED",
            not_elig,
            False,
            not_elig or ["bias_or_4h_block"],
        )
    return (
        "CONFLICTED",
        not_elig,
        False,
        not_elig or ["general_conflict"],
    )


def compute_mtf_score(
    daily_bias: str,
    s4: str,
    s30: str,
    t5: str,
    premium_zone: str,
) -> int:
    s = (
        _daily_pts(daily_bias)
        + _4h_pts(s4)
        + _30_pts(s30)
        + _5_pts(t5)
        + _pd_adjust(premium_zone)
    )
    return max(0, min(100, s))


# --------------------------------------------------------------------------- #
# Public runner
# --------------------------------------------------------------------------- #

STRATEGY_MTF = "MTF_SMC_ICT_RESEARCH"


@dataclass
class MtfCandleBundle:
    daily: list[dict[str, Any]] = field(default_factory=list)
    h4: list[dict[str, Any]] = field(default_factory=list)
    m30: list[dict[str, Any]] = field(default_factory=list)
    m5: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_mtf_smc(
    symbol: str,
    cfg: AppConfig,
    bundle: MtfCandleBundle,
    *,
    market_regime: str = "neutral",
    regime_confidence: str = "medium",
    include_5min: bool = True,
    include_daily: bool = True,
    out_eval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full MTF stack; no I/O, no IBKR. Purely uses ``bundle`` + cfg.

    If ``out_eval`` is a dict, it is filled with
    ``{"daily", "4h", "30min", "5min"}`` → :class:`StrategyEvaluation` or
    ``None`` for optional charting.
    """
    symu = symbol.upper()
    s_block = (cfg.strategies or {}).get(STRATEGY_NAME) or DEFAULT_STRATEGY_CFG
    th5 = resolve_strategy_thresholds("5min", s_block)
    w = list(bundle.warnings)
    tfd: dict[str, Any] = {}
    tfd["daily"] = {
        "loaded": bool(bundle.daily),
        "bars": len(bundle.daily),
        "bias": "unknown",
        "reason": "",
        "warnings": [],
    }
    tfd["4h"] = {
        "loaded": bool(bundle.h4),
        "bars": len(bundle.h4),
        "structure": "unknown",
        "reason": "",
        "warnings": [],
    }
    tfd["30min"] = {
        "loaded": bool(bundle.m30),
        "setup_state": "unknown",
        "entry_price": None,
        "stop_price": None,
        "target_1": None,
        "risk_reward": None,
        "warnings": [],
    }
    tfd["5min"] = {
        "loaded": bool(bundle.m5) and include_5min,
        "trigger_state": "unknown",
        "reason": "",
        "warnings": [],
    }

    bias = {"bias": "unknown", "reason": "", "evidence": {}}
    ev_d: StrategyEvaluation | None = None
    if include_daily and bundle.daily:
        bias = classify_daily_bias(bundle.daily, market_regime=market_regime)
        try:
            ev_d = evaluate_smc_liquidity_reversal(
                symu,
                bundle.daily,
                cfg=cfg,
                timeframe=normalise_timeframe("daily"),
                market_regime=market_regime,
            )
        except Exception:  # noqa: BLE001
            ev_d = None
    tfd["daily"].update(
        {
            "bias": bias.get("bias"),
            "reason": bias.get("reason", ""),
        }
    )

    ev4 = evaluate_smc_liquidity_reversal(
        symu,
        bundle.h4,
        cfg=cfg,
        timeframe=normalise_timeframe("4h"),
        market_regime=market_regime,
    )
    s4d = classify_4h_structure(bundle.h4, eval4h=ev4)
    tfd["4h"].update(
        {
            "structure": s4d.get("structure"),
            "reason": s4d.get("reason", ""),
        }
    )

    ev30 = evaluate_smc_liquidity_reversal(
        symu,
        bundle.m30,
        cfg=cfg,
        timeframe=normalise_timeframe("30min"),
        market_regime=market_regime,
    )
    setup = map_setup_30min(ev30, market_regime=market_regime)
    tfd["30min"].update(
        {
            "setup_state": setup.get("setup_state"),
            "entry_price": setup.get("entry_price"),
            "stop_price": setup.get("stop_price"),
            "target_1": setup.get("target_1"),
            "risk_reward": setup.get("risk_reward"),
            "stop_distance_pct": setup.get("stop_distance_pct"),
            "extension_pct_vs_latest_close": setup.get("extension_pct_vs_latest_close"),
            "reason": setup.get("reason", ""),
        }
    )

    ev5: StrategyEvaluation | None = None
    if include_5min and bundle.m5:
        c5: Candles = candles_from_records(bundle.m5)
        ev5 = evaluate_smc_liquidity_reversal(
            symu,
            bundle.m5,
            cfg=cfg,
            timeframe=normalise_timeframe("5min"),
            market_regime=market_regime,
        )
        t5d = classify_5min_trigger(
            ev5, c5, setup_30=setup, th5=th5
        )
        tfd["5min"].update(
            {
                "trigger_state": t5d.get("trigger_state"),
                "reason": t5d.get("reason", ""),
            }
        )
    elif not include_5min or not bundle.m5:
        tfd["5min"] = {
            "loaded": False,
            "trigger_state": "unknown",
            "reason": "5min not included or no data"
            if not include_5min
            else "missing 5m data",
            "warnings": w,
        }
    if out_eval is not None:
        out_eval.update(
            {
                "daily": ev_d,
                "4h": ev4,
                "30min": ev30,
                "5min": ev5,
            }
        )

    last_px = 0.0
    for lbl in (bundle.m30, bundle.m5, bundle.daily):
        if lbl:
            last_px = float((lbl[-1] or {}).get("close", 0) or 0)
            break
    pd = compute_premium_discount(
        rows_4h=bundle.h4, rows_30=bundle.m30, latest_price=last_px
    )
    t5_state = str(tfd.get("5min", {}).get("trigger_state", "unknown"))
    mtf = compute_mtf_score(
        str(bias.get("bias", "unknown")),
        str(s4d.get("structure", "unknown")),
        str(setup.get("setup_state", "unknown")),
        t5_state,
        str(pd.get("current_zone", "unknown")),
    )
    cat, _, _align_flag, nlong = resolve_alignment(
        daily_bias=str(bias.get("bias", "unknown")),
        s4=str(s4d.get("structure", "unknown")),
        s30=str(setup.get("setup_state", "unknown")),
        t5=t5_state,
        premium_zone=str(pd.get("current_zone", "unknown")),
        mtf_score=mtf,
    )
    m5_ok = bool(tfd.get("5min", {}).get("loaded")) and include_5min
    elig = bool(
        cat == "FULL_ALIGNMENT"
        and setup.get("setup_state") == "full_setup_valid"
        and t5_state == "confirmed"
        and m5_ok
        and market_regime not in ("risk_off", "crisis", "unknown")
    )
    not_reas = list(nlong)
    if not m5_ok and include_5min:
        not_reas.append("missing_5m_data")
    if not elig and not not_reas:
        not_reas.append("not_full_mtf_alignment")

    digest_zh = _format_zh_mtf(
        symu, bias, s4d, setup, tfd, pd, mtf, str(cat), elig, not_reas
    )

    day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "date": day_str,
        "symbol": symu,
        "strategy": STRATEGY_MTF,
        "research_only": True,
        "execution_allowed": False,
        "market_regime": market_regime,
        "regime_confidence": regime_confidence,
        "timeframes": tfd,
        "premium_discount": pd,
        "mtf_alignment_score": mtf,
        "alignment_category": cat,
        "eligible_for_future_paper_trade": elig,
        "not_eligible_reasons": not_reas,
        "mtf_bias_daily": bias,
        "mtf_structure_4h": s4d,
        "mtf_setup_30": setup,
        "warnings": w,
        "chart_paths": [],
        "human_summary_zh": digest_zh,
    }


def _format_zh_mtf(
    sym: str,
    bias: dict,
    s4: dict,
    setup: dict,
    tfd: dict,
    pd: dict,
    mtf: int,
    cat: str,
    elig: bool,
    nlong: list,
) -> str:
    lines = [
        f"【MTF SMC/ICT 多周期识别】{sym}",
        f"日图偏向：{bias.get('bias', '-')}",
        f"4H 结构：{s4.get('structure', '-')}",
        f"30m 设置：{setup.get('setup_state', '-')}",
        f"5m 触发：{tfd.get('5min', {}).get('trigger_state', '-')}",
        f"溢价/折扣：{pd.get('current_zone', '-')}",
        f"多周期分数：{mtf} / 100",
        f"分类：{cat}",
        f"未来纸面候选(仅研究，不下单）：{'是' if elig else '否'}",
    ]
    if nlong:
        lines.append("不符合原因：" + "；".join(nlong[:6]))
    lines.append("提醒：研究扫描，不下单。execution_allowed=false。")
    return "\n".join(lines)


def _telegram_watchlist_header_zh() -> str:
    return "【MTF SMC/ICT 多周期扫描】"


def format_mtf_watchlist_digest_zh(summary: dict[str, Any]) -> str:
    """Plain-text Chinese summary for watchlist ``scan-mtf-smc-watchlist``."""
    title = _telegram_watchlist_header_zh()
    day = str(summary.get("date") or "")
    n = int(summary.get("symbols_scanned") or 0)
    counts = summary.get("counts") or {}
    lines = [f"{title}{day}", f"已扫描：{n} 个代码", "分类计数："]
    for k in (
        "FULL_ALIGNMENT",
        "SETUP_READY_WAITING_TRIGGER",
        "BIAS_OK_SETUP_INCOMPLETE",
        "CONFLICTED",
        "BLOCKED",
    ):
        lines.append(f"  {k}: {counts.get(k, 0)}")
    top5 = summary.get("top_by_alignment_score") or []
    lines.append("分数领先 Top5：")
    for t in top5[:5]:
        lines.append(
            f"  {t.get('symbol', '')}  score={t.get('mtf_alignment_score', 0)}  "
            f"cat={t.get('alignment_category', '')}"
        )
    full_n = [i for i in (summary.get("items") or []) if i.get("alignment_category") == "FULL_ALIGNMENT"]
    lines.append("FULL_ALIGNMENT：" + (", ".join(x["symbol"] for x in full_n) or "（无）"))
    wait_t = [i for i in (summary.get("items") or []) if i.get("alignment_category") == "SETUP_READY_WAITING_TRIGGER"]
    lines.append("SETUP_READY_WAITING_TRIGGER：" + (", ".join(x["symbol"] for x in wait_t) or "（无）"))
    bad = [i for i in (summary.get("items") or []) if i.get("alignment_category") in ("BLOCKED", "CONFLICTED")]
    lines.append("BLOCKED/CONFLICTED：" + (", ".join(x["symbol"] for x in bad[:20]) or "（无）"))
    if not counts.get("FULL_ALIGNMENT"):
        lines.append("暂无完全符合多周期 SMC/ICT 条件的候选。系统未下单。")
    lines.append("研究扫描，不下单。execution_allowed=false。research_only=true。")
    return "\n".join(lines)


__all__ = [
    "MtfCandleBundle",
    "STRATEGY_MTF",
    "classify_5min_trigger",
    "classify_daily_bias",
    "classify_4h_structure",
    "compute_mtf_score",
    "compute_premium_discount",
    "format_mtf_watchlist_digest_zh",
    "map_setup_30min",
    "resolve_alignment",
    "run_mtf_smc",
]
