"""Strategy engine (V0).

This module hosts the *research-only* SMC liquidity-reversal evaluator.
It assembles primitives from :mod:`bot.market_structure` into a
deterministic dry-run plan and a list of rejection reasons.

Hard invariants for V0
----------------------
* ``execution_allowed`` is always ``False`` in the returned payload.
* The evaluator never imports :class:`bot.broker.Broker.place_order`,
  never opens a socket, never queues an order. It is a pure function of
  ``(candles, market_regime, account_equity, latest_close, cfg)``.
* Risk gates from the SMC strategy block (``max_allowed_stop_pct``,
  ``max_account_risk_per_trade_pct``, R/R floor, regime block-list,
  price-extension cap) can only *add* rejection reasons; they cannot
  bypass any of the existing safety layers (``trading.enabled``,
  reconciliation gate, ``block_live_trading``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig
from .smc_timeframes import (
    apply_thresholds_to_block,
    normalise_timeframe,
    resolve_strategy_thresholds,
)
from .market_structure import (
    Candles,
    calculate_structural_stop,
    candles_from_records,
    detect_bullish_fvg,
    detect_bullish_order_block,
    detect_choch_after_sweep,
    detect_liquidity_sweep,
    detect_swing_highs,
    detect_swing_lows,
    select_fvg_for_setup,
    select_target_1,
)

STRATEGY_NAME = "SMC_LIQUIDITY_REVERSAL_RESEARCH"

# Minimum reward-to-risk ratio for an approved dry-run plan.
DEFAULT_MIN_RR = 2.0

# Default config block - mirrors ``config/strategy.yaml`` and is used
# when the AppConfig does not yet declare the strategy.
DEFAULT_STRATEGY_CFG: dict[str, Any] = {
    "enabled": True,
    "research_only": True,
    "execution_allowed": False,
    "dry_run_only": True,
    "market_filter": {"block_if_market_regime": ["risk_off", "crisis", "unknown"]},
    "swing_detection": {"left_bars": 2, "right_bars": 2},
    "sweep": {
        "lookback_period": 20,
        "require_close_back_above_swept_low": True,
        "allow_intraday_wick_sweep": True,
    },
    "choch": {"max_bars_after_sweep": 10, "require_close_above_pivot_high": True},
    "fvg": {
        "require_fvg": True,
        "min_fvg_size_pct": 0.10,
        "max_fvg_distance_from_choch_bars": 3,
    },
    "order_block": {"enabled": True, "method": "last_down_close_before_choch"},
    "entry": {
        "type": "limit_at_fvg_top",
        "max_days_to_fill_limit": 3,
        "reject_if_price_extended_from_entry_pct": 3,
    },
    "stop": {"type": "structural", "buffer_cents": 0.05, "max_allowed_stop_pct": 5.0},
    "risk": {
        "max_account_risk_per_trade_pct": 1.0,
        "max_equity_per_position_pct": 10,
        "min_reward_to_risk": DEFAULT_MIN_RR,
    },
    "profit_management": {
        "target_method": "prior_swing_high",
        "partial_sell_pct": 50,
        "move_stop_to_breakeven_after_partial": True,
        "trail_method": "close_below_ema",
        "trail_ema_period": 10,
    },
    # Target 1 selection (V1). ``nearest_buy_side_liquidity`` replaces
    # the V0 "highest prior swing high" behaviour which produced
    # unrealistically optimistic R/R on extended uptrends.
    "target": {
        "method": "nearest_buy_side_liquidity",
        "lookback_bars_before_sweep": 60,
        "max_target_distance_pct": 25.0,
        "min_risk_reward": DEFAULT_MIN_RR,
    },
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class StrategyEvaluation:
    """Structured evaluator output. Always JSON-serialisable.

    The ``detected_levels`` block is a flat dict of the price levels a
    human reviewer typically annotates on a chart. ``chart_path`` is
    populated by the CLI after rendering (the evaluator itself never
    draws). ``validation_notes`` is a free-form list reviewers can
    append to during manual chart inspection - we initialise it with
    automated reminders such as "FVG very small" but never clear it.
    """

    strategy: str
    symbol: str
    timeframe: str
    approved_for_dry_run: bool
    execution_allowed: bool  # MUST be False in V0
    sequence: dict[str, Any]
    trade_plan: dict[str, Any] | None
    rejection_reasons: list[str]
    market_regime: str | None = None
    candle_count: int = 0
    notes: list[str] = field(default_factory=list)
    candles_start: str | None = None
    candles_end: str | None = None
    detected_levels: dict[str, float | None] = field(default_factory=dict)
    validation_notes: list[str] = field(default_factory=list)
    target_debug: dict[str, Any] = field(default_factory=dict)
    chart_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "approved_for_dry_run": self.approved_for_dry_run,
            "execution_allowed": self.execution_allowed,
            "market_regime": self.market_regime,
            "candle_count": self.candle_count,
            "candles_start": self.candles_start,
            "candles_end": self.candles_end,
            "sequence": self.sequence,
            "trade_plan": self.trade_plan,
            "detected_levels": dict(self.detected_levels),
            "target_debug": dict(self.target_debug),
            "rejection_reasons": list(self.rejection_reasons),
            "validation_notes": list(self.validation_notes),
            "notes": list(self.notes),
            "chart_path": self.chart_path,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def evaluate_smc_liquidity_reversal(
    symbol: str,
    candles: list[dict[str, Any]] | Candles,
    *,
    timeframe: str = "daily",
    cfg: AppConfig | None = None,
    market_regime: str | None = "neutral",
    account_equity: float | None = None,
    latest_close: float | None = None,
) -> StrategyEvaluation:
    """Run the SMC liquidity-reversal *research* evaluator.

    The evaluator is intentionally cautious: it returns
    ``approved_for_dry_run=False`` whenever any single rule rejects the
    setup. Even when the dry-run plan is approved, ``execution_allowed``
    is hard-coded to ``False`` so no execution path can pick this up.
    """
    typed_candles: Candles = (
        list(candles)  # already Candles - keep as-is
        if candles and not isinstance(candles[0], dict)
        else candles_from_records(candles)  # type: ignore[arg-type]
    )

    # Normalise the timeframe early so every downstream caller sees the
    # canonical label. Unknown labels fall back to ``daily``.
    timeframe = normalise_timeframe(timeframe)
    sm = _strategy_block(cfg, timeframe=timeframe)
    rejections: list[str] = []
    notes: list[str] = []

    # 1. Regime gate (cheapest check first).
    regime_block_list = (sm.get("market_filter") or {}).get(
        "block_if_market_regime"
    ) or []
    if market_regime in regime_block_list:
        rejections.append(f"market_regime={market_regime} blocks new setups")

    # 2. Swing detection.
    swing_cfg = sm.get("swing_detection") or {}
    left = int(swing_cfg.get("left_bars", 2))
    right = int(swing_cfg.get("right_bars", 2))
    if len(typed_candles) < (left + right + 5):
        rejections.append(
            f"insufficient_candles ({len(typed_candles)}); "
            f"need at least {left + right + 5}"
        )
        return _build(
            symbol=symbol,
            timeframe=timeframe,
            sequence=_empty_sequence(),
            trade_plan=None,
            rejections=rejections,
            market_regime=market_regime,
            candles=typed_candles,
            notes=notes,
        )

    swings_low = detect_swing_lows(typed_candles, left=left, right=right)
    swings_high = detect_swing_highs(typed_candles, left=left, right=right)

    # 3. Liquidity sweep.
    sweep_cfg = sm.get("sweep") or {}
    sweeps = detect_liquidity_sweep(
        typed_candles,
        lookback=int(sweep_cfg.get("lookback_period", 20)),
        swings=swings_low,
        require_close_back_above=bool(
            sweep_cfg.get("require_close_back_above_swept_low", True)
        ),
    )
    sweep = sweeps[-1] if sweeps else None
    if sweep is None:
        rejections.append("no_liquidity_sweep")

    # 4. ChoCH after sweep.
    choch_cfg = sm.get("choch") or {}
    choch = (
        detect_choch_after_sweep(
            typed_candles,
            sweep,
            max_bars_after_sweep=int(choch_cfg.get("max_bars_after_sweep", 10)),
            require_close_above_pivot_high=bool(
                choch_cfg.get("require_close_above_pivot_high", True)
            ),
            swings=swings_high,
        )
        if sweep
        else None
    )
    if sweep and choch is None:
        rejections.append("no_choch_after_sweep")

    # 5. Bullish FVG.
    fvg_cfg = sm.get("fvg") or {}
    require_fvg = bool(fvg_cfg.get("require_fvg", True))
    fvg = None
    if sweep and choch:
        fvg = select_fvg_for_setup(
            typed_candles,
            sweep,
            choch,
            min_size_pct=float(fvg_cfg.get("min_fvg_size_pct", 0.10)),
            max_distance_from_choch_bars=int(
                fvg_cfg.get("max_fvg_distance_from_choch_bars", 3)
            ),
        )
    if require_fvg and choch and fvg is None:
        rejections.append("no_bullish_fvg")

    # 6. Order block (last down-close before ChoCH impulse).
    ob_cfg = sm.get("order_block") or {}
    ob_required = bool(ob_cfg.get("enabled", True))
    ob = detect_bullish_order_block(typed_candles, choch) if choch else None
    if ob_required and choch and ob is None:
        rejections.append("no_order_block")

    sequence = {
        "sweep": _normalise_sequence_entry(sweep),
        "choch": _normalise_sequence_entry(choch),
        "fvg": _normalise_sequence_entry(fvg),
        "order_block": _normalise_sequence_entry(ob),
    }

    # 7. Build the trade plan only when the structural pieces are in
    #    place. We still attach the partial sequence above so operators
    #    can debug rejected setups.
    trade_plan = None
    if sweep and choch and fvg and ob:
        trade_plan, plan_rejections, plan_notes = _build_trade_plan(
            sweep=sweep,
            choch=choch,
            fvg=fvg,
            order_block=ob,
            candles=typed_candles,
            swings_high=swings_high,
            cfg_block=sm,
            account_equity=account_equity,
            latest_close=latest_close,
        )
        rejections.extend(plan_rejections)
        notes.extend(plan_notes)

    return _build(
        symbol=symbol,
        timeframe=timeframe,
        sequence=sequence,
        trade_plan=trade_plan,
        rejections=rejections,
        market_regime=market_regime,
        candles=typed_candles,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Trade-plan helper
# ---------------------------------------------------------------------------
def _build_trade_plan(
    *,
    sweep: dict[str, Any],
    choch: dict[str, Any],
    fvg: dict[str, Any],
    order_block: dict[str, Any],
    candles: Candles,
    swings_high: list,
    cfg_block: dict[str, Any],
    account_equity: float | None,
    latest_close: float | None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    rejections: list[str] = []
    notes: list[str] = []

    entry_cfg = cfg_block.get("entry") or {}
    stop_cfg = cfg_block.get("stop") or {}
    risk_cfg = cfg_block.get("risk") or {}

    entry_type = str(entry_cfg.get("type", "limit_at_fvg_top"))
    if entry_type == "limit_at_ob_top":
        entry_price = float(order_block["high"])
        entry_zone = {"low": order_block["low"], "high": order_block["high"]}
    else:
        entry_type = "limit_at_fvg_top"
        entry_price = float(fvg["high"])
        entry_zone = {"low": fvg["low"], "high": fvg["high"]}

    structural_stop = calculate_structural_stop(
        {"sweep": sweep, "order_block": order_block},
        buffer_cents=float(stop_cfg.get("buffer_cents", 0.05)),
    )
    risk_per_share = round(entry_price - structural_stop, 4)
    if risk_per_share <= 0:
        rejections.append("risk_per_share_non_positive")
    stop_distance_pct = (
        round((risk_per_share / entry_price) * 100.0, 4)
        if entry_price > 0 and risk_per_share > 0
        else 0.0
    )
    max_stop_pct = float(stop_cfg.get("max_allowed_stop_pct", 5.0))
    if stop_distance_pct > max_stop_pct:
        rejections.append(
            f"stop_distance_pct {stop_distance_pct:.2f} > max {max_stop_pct:.2f}"
        )

    target_cfg = cfg_block.get("target") or {}
    min_rr = float(risk_cfg.get("min_reward_to_risk", DEFAULT_MIN_RR))
    # ``target.min_risk_reward`` shadows ``risk.min_reward_to_risk`` for
    # target screening; ``risk.min_reward_to_risk`` is still the
    # ultimate gate so operators can set two thresholds if they want.
    target_min_rr = float(target_cfg.get("min_risk_reward", min_rr))
    max_distance_pct = float(target_cfg.get("max_target_distance_pct", 25.0))
    lookback = int(target_cfg.get("lookback_bars_before_sweep", 60))
    target_method = str(target_cfg.get("method", "nearest_buy_side_liquidity"))

    target_1, target_candidates, target_rejection = select_target_1(
        candles,
        sweep,
        choch,
        entry_price=entry_price,
        risk_per_share=risk_per_share if risk_per_share > 0 else 0.0,
        swings_high=swings_high,
        lookback_bars_before_sweep=lookback,
        max_target_distance_pct=max_distance_pct,
        min_risk_reward=target_min_rr,
    )
    target_debug = {
        "method": target_method,
        "candidates": target_candidates,
        "rejection_reason": target_rejection,
        "lookback_bars_before_sweep": lookback,
        "max_target_distance_pct": max_distance_pct,
        "min_risk_reward": target_min_rr,
    }

    if target_rejection == "no_target_above_entry":
        rejections.append("target_1_not_above_entry")
        rr = 0.0
    elif target_rejection == "target_1_too_far":
        rejections.append(
            f"target_1_too_far (> {max_distance_pct:.2f}% from entry)"
        )
        rr = 0.0
    elif target_rejection == "no_target_meets_min_rr":
        rejections.append(
            f"r_r_to_target_1 below min {target_min_rr:.2f} for all candidates"
        )
        rr = 0.0
    elif target_1 is None:
        rejections.append("no_target_1_swing_high")
        rr = 0.0
    elif risk_per_share <= 0:
        rr = 0.0
    else:
        rr = round((target_1 - entry_price) / risk_per_share, 4)

    if target_1 is not None and risk_per_share > 0 and rr < min_rr:
        rejections.append(f"r_r_to_target_1 {rr:.2f} < {min_rr:.2f}")

    qty_by_risk = 0
    position_value = 0.0
    if account_equity and account_equity > 0 and risk_per_share > 0:
        max_dollar_risk = account_equity * (
            float(risk_cfg.get("max_account_risk_per_trade_pct", 1.0)) / 100.0
        )
        qty_by_risk = int(math.floor(max_dollar_risk / risk_per_share))
        position_value = qty_by_risk * entry_price
        max_position_value = account_equity * (
            float(risk_cfg.get("max_equity_per_position_pct", 10)) / 100.0
        )
        if qty_by_risk <= 0:
            rejections.append("qty_by_risk_non_positive")
        elif position_value > max_position_value:
            # Shrink to the per-position cap rather than reject outright -
            # but flag it so the operator notices.
            old_qty = qty_by_risk
            qty_by_risk = int(math.floor(max_position_value / entry_price))
            position_value = qty_by_risk * entry_price
            notes.append(
                f"qty trimmed from {old_qty} to {qty_by_risk} by "
                f"max_equity_per_position_pct"
            )
    elif account_equity is None:
        notes.append("account_equity not provided; qty_by_risk not computed")

    # Anti-chasing: reject if current price is already too far above the
    # planned limit entry. ``latest_close`` is optional.
    extension_pct = None
    if latest_close is not None and entry_price > 0:
        extension_pct = round(
            ((latest_close - entry_price) / entry_price) * 100.0, 4
        )
        max_ext = float(
            entry_cfg.get("reject_if_price_extended_from_entry_pct", 3)
        )
        if extension_pct > max_ext:
            rejections.append(
                f"price_extended_from_entry_pct {extension_pct:.2f} > {max_ext:.2f} "
                "(no chasing)"
            )

    plan = {
        "entry_type": entry_type,
        "entry_price": round(entry_price, 4),
        "entry_zone": entry_zone,
        "structural_stop": structural_stop,
        "risk_per_share": risk_per_share,
        "stop_distance_pct": stop_distance_pct,
        "target_1": round(float(target_1), 4) if target_1 is not None else None,
        "risk_reward_to_target_1": rr,
        "target_debug": target_debug,
        "qty_by_risk": qty_by_risk,
        "position_value": round(position_value, 2),
        "extension_pct_vs_latest_close": extension_pct,
        # The next two fields are deliberate, redundant, and read by
        # downstream tests + docs to make the safety contract obvious.
        "execution_allowed": False,
        "research_only": True,
    }
    return plan, rejections, notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strategy_block(
    cfg: AppConfig | None, *, timeframe: str = "daily"
) -> dict[str, Any]:
    """Resolve the SMC strategy config block for ``timeframe``.

    The resolution order is:

    1. Start from :data:`DEFAULT_STRATEGY_CFG`.
    2. Shallow-merge the ``strategies.SMC_LIQUIDITY_REVERSAL_RESEARCH``
       block from ``config/strategy.yaml`` (if present).
    3. Apply the per-timeframe thresholds from
       :func:`bot.smc_timeframes.resolve_strategy_thresholds` on top of
       the nested sub-blocks (``sweep`` / ``stop`` / ``entry`` /
       ``risk`` / ``target``). This is how the 30min profile tightens
       ``max_allowed_stop_pct``, ``min_risk_reward``, ``max_extension
       _pct`` and ``risk_per_trade_pct``.

    Note: the function is pure — it never reads from disk, never
    opens an IBKR socket, and never calls :meth:`bot.broker.Broker.
    place_order` (which itself refuses to send anything in V0).
    """
    if cfg is None:
        raw_block = None
    else:
        raw = getattr(cfg, "strategies", None) or {}
        raw_block = raw.get(STRATEGY_NAME) if isinstance(raw, dict) else None

    merged = {**DEFAULT_STRATEGY_CFG}
    if raw_block:
        for k, v in raw_block.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v

    thresholds = resolve_strategy_thresholds(timeframe, merged)
    return apply_thresholds_to_block(merged, thresholds)


def _normalise_sequence_entry(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {"found": False}
    base = {"found": True}
    base.update(entry)
    return base


def _empty_sequence() -> dict[str, Any]:
    return {
        "sweep": {"found": False},
        "choch": {"found": False},
        "fvg": {"found": False},
        "order_block": {"found": False},
    }


def _build(
    *,
    symbol: str,
    timeframe: str,
    sequence: dict[str, Any],
    trade_plan: dict[str, Any] | None,
    rejections: list[str],
    market_regime: str | None,
    candles: Candles,
    notes: list[str],
) -> StrategyEvaluation:
    approved = (not rejections) and trade_plan is not None
    candles_start = candles[0].timestamp if candles else None
    candles_end = candles[-1].timestamp if candles else None
    detected_levels = _build_detected_levels(sequence, trade_plan)
    validation_notes = _auto_validation_notes(sequence, trade_plan, candles)
    plan_debug = (trade_plan or {}).get("target_debug") or {}
    target_debug = {
        "method": plan_debug.get("method", "nearest_buy_side_liquidity"),
        "candidates": list(plan_debug.get("candidates") or []),
        "rejection_reason": plan_debug.get("rejection_reason"),
        "lookback_bars_before_sweep": plan_debug.get("lookback_bars_before_sweep"),
        "max_target_distance_pct": plan_debug.get("max_target_distance_pct"),
        "min_risk_reward": plan_debug.get("min_risk_reward"),
    }
    return StrategyEvaluation(
        strategy=STRATEGY_NAME,
        symbol=symbol,
        timeframe=timeframe,
        approved_for_dry_run=approved,
        execution_allowed=False,  # V0 hard rule
        sequence=sequence,
        trade_plan=trade_plan,
        rejection_reasons=rejections,
        market_regime=market_regime,
        candle_count=len(candles),
        candles_start=candles_start,
        candles_end=candles_end,
        detected_levels=detected_levels,
        validation_notes=validation_notes,
        target_debug=target_debug,
        notes=notes,
        chart_path=None,
    )


def _build_detected_levels(
    sequence: dict[str, Any], plan: dict[str, Any] | None
) -> dict[str, float | None]:
    """Flatten the structural levels reviewers annotate on a chart."""
    sweep = sequence.get("sweep") or {}
    choch = sequence.get("choch") or {}
    fvg = sequence.get("fvg") or {}
    ob = sequence.get("order_block") or {}
    plan = plan or {}
    return {
        "swept_low": _maybe_float(sweep.get("swept_low_price")),
        "sweep_low": _maybe_float(sweep.get("sweep_low")),
        "choch_pivot": _maybe_float(choch.get("pivot_high_broken")),
        "choch_close": _maybe_float(choch.get("close")),
        "fvg_low": _maybe_float(fvg.get("low")),
        "fvg_high": _maybe_float(fvg.get("high")),
        "ob_low": _maybe_float(ob.get("low")),
        "ob_high": _maybe_float(ob.get("high")),
        "entry": _maybe_float(plan.get("entry_price")),
        "stop": _maybe_float(plan.get("structural_stop")),
        "target_1": _maybe_float(plan.get("target_1")),
    }


def _auto_validation_notes(
    sequence: dict[str, Any],
    plan: dict[str, Any] | None,
    candles: Candles,
) -> list[str]:
    """Pre-fill helpful hints for the human chart reviewer.

    These notes are *advisory only* - they never gate execution. They
    are meant to draw the reviewer's eye to common failure modes
    (very small FVG, sweep that is suspiciously deep, etc.).
    """
    notes: list[str] = []
    fvg = sequence.get("fvg") or {}
    if fvg.get("found") and isinstance(fvg.get("size_pct"), (int, float)):
        if fvg["size_pct"] < 0.20:
            notes.append(
                f"FVG size_pct={fvg['size_pct']:.3f}% is very small; "
                "double-check on the chart."
            )
    sweep = sequence.get("sweep") or {}
    if sweep.get("found") and candles:
        ref = sweep.get("swept_low_price")
        sw_low = sweep.get("sweep_low")
        if (
            isinstance(ref, (int, float))
            and isinstance(sw_low, (int, float))
            and ref > 0
        ):
            depth_pct = (ref - sw_low) / ref * 100.0
            if depth_pct > 3.0:
                notes.append(
                    f"sweep depth {depth_pct:.2f}% below swept low is large; "
                    "verify it isn't a regime change."
                )
    if plan and isinstance(plan.get("risk_reward_to_target_1"), (int, float)):
        if 0 < plan["risk_reward_to_target_1"] < 2.0:
            notes.append(
                "R/R below 2.0; setup will be rejected by the engine."
            )
    return notes


def _maybe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


__all__ = [
    "STRATEGY_NAME",
    "DEFAULT_STRATEGY_CFG",
    "StrategyEvaluation",
    "evaluate_smc_liquidity_reversal",
]
