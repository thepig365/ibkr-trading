"""Detection primitives for ICT/SMC Intraday Liquidity Reversal V1.

Pure functions on lists of OHLCV dicts. NO broker / IBKR / network
imports. NO order placement. NO live-trading wiring.

Reuses bullish primitives from :mod:`bot.market_structure` (sweep,
ChoCH, FVG, OB) and adds bearish counterparts here so we do not
modify the proven swing-based primitives. Bearish helpers mirror the
bullish ones algorithmically — ``swept_high``, ``close <= swept_high``,
``low_pivot_break``, etc.

Public surface used by :mod:`.scanner`:

* :func:`build_intraday_context`
* :func:`detect_5m_setup`
* :func:`detect_1m_entry_trigger`

Lookahead safety
----------------
* All detectors operate on completed bars only (the caller may drop
  the most recent bar if it suspects partial data; the live scan
  fetches at scan time so the latest bar is "now-ish").
* Swing confirmation uses ``left_bars=2`` / ``right_bars=2`` from the
  reused primitives.
"""

from __future__ import annotations

from typing import Any

from ...market_structure import (
    Candle,
    SwingPoint,
    candles_from_records,
    detect_bullish_fvg,
    detect_bullish_order_block,
    detect_choch_after_sweep,
    detect_liquidity_sweep,
    detect_swing_highs,
    detect_swing_lows,
)
from .model import (
    DIRECTION_FLAT,
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_SOURCE_BREAKER,
    ENTRY_SOURCE_FVG,
    ENTRY_SOURCE_NONE,
    ENTRY_SOURCE_OB,
    FiveMinuteSetup,
    IntradayContext,
    LiquidityLevel,
    OneMinuteTrigger,
)


# ---------------------------------------------------------------------------
# Bearish counterparts (kept here so market_structure.py stays untouched)
# ---------------------------------------------------------------------------
def detect_bearish_liquidity_sweep(
    candles: list[Candle],
    lookback: int = 30,
    *,
    swings_high: list[SwingPoint] | None = None,
    require_close_back_below: bool = True,
) -> list[dict[str, Any]]:
    """Bearish twin of :func:`detect_liquidity_sweep`.

    A bearish sweep prints *above* the highest confirmed prior swing
    high inside the lookback window then closes back below it.
    """
    swings_high = swings_high or detect_swing_highs(candles)
    confirmed_highs = [s for s in swings_high if s.confirmed]
    out: list[dict[str, Any]] = []
    for i in range(1, len(candles)):
        c = candles[i]
        candidates = [
            s for s in confirmed_highs
            if s.index < i and (i - s.index) <= lookback
        ]
        if not candidates:
            continue
        swept = max(candidates, key=lambda s: s.price)
        if c.high <= swept.price:
            continue
        if require_close_back_below and c.close > swept.price:
            continue
        out.append({
            "found": True,
            "timestamp": c.timestamp,
            "index": i,
            "swept_high_index": swept.index,
            "swept_high_price": swept.price,
            "sweep_high": c.high,
            "close": c.close,
            "closed_back_below": c.close <= swept.price,
        })
    return out


def detect_bearish_choch_after_sweep(
    candles: list[Candle],
    sweep_event: dict[str, Any] | None,
    *,
    max_bars_after_sweep: int = 10,
    swings_low: list[SwingPoint] | None = None,
    require_close_below_pivot_low: bool = True,
) -> dict[str, Any] | None:
    """Bearish twin of :func:`detect_choch_after_sweep`.

    After a bearish sweep, the first close *below* the most recent
    confirmed pivot low is the bearish ChoCH.
    """
    if not sweep_event:
        return None
    swings_low = swings_low or detect_swing_lows(candles)
    confirmed_lows = [
        s for s in swings_low if s.confirmed and s.index < sweep_event["index"]
    ]
    if not confirmed_lows:
        return None
    pivot = max(confirmed_lows, key=lambda s: s.index)
    start = sweep_event["index"] + 1
    end = min(start + max_bars_after_sweep, len(candles))
    for j in range(start, end):
        c = candles[j]
        broke = (
            (c.close < pivot.price) if require_close_below_pivot_low
            else (c.low < pivot.price)
        )
        if broke:
            return {
                "found": True,
                "timestamp": c.timestamp,
                "index": j,
                "pivot_low_index": pivot.index,
                "pivot_low_broken": pivot.price,
                "close": c.close,
                "bars_after_sweep": j - sweep_event["index"],
            }
    return None


def detect_bearish_fvg(candles: list[Candle]) -> list[dict[str, Any]]:
    """Bearish 3-candle imbalance: ``candles[i].high < candles[i-2].low``."""
    out: list[dict[str, Any]] = []
    for i in range(2, len(candles)):
        c1 = candles[i - 2]
        c3 = candles[i]
        if c3.high < c1.low:
            zone_low = c3.high
            zone_high = c1.low
            ref = c1.low if c1.low > 0 else (c3.high or 1.0)
            size_pct = ((zone_high - zone_low) / ref) * 100.0 if ref > 0 else 0.0
            out.append({
                "found": True,
                "start_index": i - 2,
                "end_index": i,
                "timestamp": candles[i - 1].timestamp,
                "low": zone_low,
                "high": zone_high,
                "size_pct": round(size_pct, 4),
            })
    return out


def detect_bearish_order_block(
    candles: list[Candle],
    choch_event: dict[str, Any] | None,
    *,
    max_lookback: int = 20,
) -> dict[str, Any] | None:
    """Last bullish (close > open) candle before a bearish ChoCH."""
    if not choch_event:
        return None
    j = int(choch_event["index"])
    start = max(0, j - max_lookback)
    for k in range(j - 1, start - 1, -1):
        c = candles[k]
        if c.close > c.open:
            return {
                "found": True,
                "index": k,
                "timestamp": c.timestamp,
                "low": c.low,
                "high": c.high,
                "open": c.open,
                "close": c.close,
            }
    return None


# ---------------------------------------------------------------------------
# Higher-timeframe context
# ---------------------------------------------------------------------------
def _bias_from_bars(bars: list[dict[str, Any]], lookback: int = 20) -> str:
    """Soft bias based on close vs SMA(lookback). Used for 4H/30m/5m hints.

    Returns ``"up"`` / ``"down"`` / ``"neutral"`` / ``"unknown"``.
    """
    if not bars or len(bars) < 5:
        return "unknown"
    closes = [float(b.get("close", 0.0) or 0.0) for b in bars[-lookback:]]
    if len(closes) < 5:
        return "unknown"
    sma = sum(closes) / len(closes)
    last = closes[-1]
    if sma <= 0:
        return "unknown"
    diff_pct = (last - sma) / sma * 100.0
    if diff_pct >= 0.25:
        return "up"
    if diff_pct <= -0.25:
        return "down"
    return "neutral"


def _premium_discount(bars_30m: list[dict[str, Any]], lookback: int = 60) -> str:
    """Where last close sits inside the last ``lookback`` 30m range."""
    if not bars_30m:
        return "unknown"
    window = bars_30m[-lookback:]
    if len(window) < 5:
        return "unknown"
    hi = max(float(b.get("high", 0.0) or 0.0) for b in window)
    lo = min(float(b.get("low", 0.0) or 0.0) for b in window)
    if hi <= lo:
        return "unknown"
    last = float(window[-1].get("close", 0.0) or 0.0)
    mid = (hi + lo) / 2.0
    if last >= mid:
        return "premium"
    return "discount"


def _liquidity_levels(
    bars_30m: list[dict[str, Any]],
    *,
    last_close: float | None,
) -> list[LiquidityLevel]:
    """Pick the two most relevant 30m liquidity levels (one above, one below)."""
    out: list[LiquidityLevel] = []
    if not bars_30m or last_close is None:
        return out
    try:
        candles = candles_from_records(bars_30m[-100:])
    except Exception:  # noqa: BLE001
        return out
    sw_high = detect_swing_highs(candles, left=2, right=2)
    sw_low = detect_swing_lows(candles, left=2, right=2)
    above = [s for s in sw_high if s.confirmed and s.price > last_close]
    below = [s for s in sw_low if s.confirmed and s.price < last_close]
    if above:
        nearest_high = min(above, key=lambda s: s.price - last_close)
        out.append(
            LiquidityLevel(
                side="buy_side",
                price=round(float(nearest_high.price), 4),
                timestamp=str(nearest_high.timestamp),
                timeframe="30min",
            )
        )
    if below:
        nearest_low = min(below, key=lambda s: last_close - s.price)
        out.append(
            LiquidityLevel(
                side="sell_side",
                price=round(float(nearest_low.price), 4),
                timestamp=str(nearest_low.timestamp),
                timeframe="30min",
            )
        )
    return out


def build_intraday_context(
    symbol: str,
    bars_4h: list[dict[str, Any]] | None,
    bars_30m: list[dict[str, Any]] | None,
    bars_5m: list[dict[str, Any]] | None,
    *,
    bars_1m_count: int = 0,
    data_source: str = "unknown",
) -> IntradayContext:
    """Assemble higher-timeframe context (soft only).

    Missing 4H => bias_4h="unknown" + note. Missing 30m or 5m =>
    same. None of these are hard blockers — the strategy degrades to
    "unknown" + a note, never raises.
    """
    bars_4h = bars_4h or []
    bars_30m = bars_30m or []
    bars_5m = bars_5m or []
    notes: list[str] = []
    missing: list[str] = []
    if not bars_4h:
        notes.append("4H bars unavailable; bias_4h=unknown.")
        missing.append("4h")
    if not bars_30m:
        notes.append("30m bars unavailable; intraday liquidity map degraded.")
        missing.append("30min")
    if not bars_5m:
        notes.append("5m bars unavailable; setup detection will fail.")
        missing.append("5min")

    last_5m_close: float | None = None
    if bars_5m:
        try:
            last_5m_close = float(bars_5m[-1].get("close", 0.0) or 0.0)
        except (TypeError, ValueError):
            last_5m_close = None

    return IntradayContext(
        symbol=symbol.upper(),
        bias_4h=_bias_from_bars(bars_4h, lookback=20),
        bias_30m=_bias_from_bars(bars_30m, lookback=20),
        bias_5m=_bias_from_bars(bars_5m, lookback=20),
        premium_discount_30m=_premium_discount(bars_30m, lookback=60),
        liquidity_levels=_liquidity_levels(bars_30m, last_close=last_5m_close),
        notes=notes,
        bars_4h_count=len(bars_4h),
        bars_30m_count=len(bars_30m),
        bars_5m_count=len(bars_5m),
        bars_1m_count=int(bars_1m_count or 0),
        data_source=data_source,
        missing_data=missing,
    )


# ---------------------------------------------------------------------------
# 5m setup detection
# ---------------------------------------------------------------------------
def _setup_kind(has_fvg: bool, has_ob: bool) -> str:
    if has_fvg:
        return ENTRY_SOURCE_FVG
    if has_ob:
        return ENTRY_SOURCE_OB
    return ENTRY_SOURCE_BREAKER  # reclaim zone fallback


def _zone_from_fvg(fvg: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not fvg:
        return None, None
    return float(fvg.get("low", 0.0) or 0.0), float(fvg.get("high", 0.0) or 0.0)


def _zone_from_ob(ob: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not ob:
        return None, None
    return float(ob.get("low", 0.0) or 0.0), float(ob.get("high", 0.0) or 0.0)


def detect_5m_setup(
    bars_5m: list[dict[str, Any]] | None,
    context: IntradayContext,
    *,
    sweep_lookback: int = 30,
    direction_hint: str = "auto",
) -> FiveMinuteSetup:
    """Detect a 5m sweep + reclaim setup zone.

    ``direction_hint`` may be ``"long"``, ``"short"`` or ``"auto"`` —
    when ``"auto"`` we try long first then short and return the most
    recent setup. The 5m setup does NOT require an FVG; the prompt
    says ``require_fvg: false`` for 5m. MSS/ChoCH is preferred but
    not mandatory for WATCH classification (the classifier handles
    that).
    """
    setup = FiveMinuteSetup()
    if not bars_5m or len(bars_5m) < 6:
        setup.rejection_reasons.append("5m: not enough bars")
        return setup
    try:
        candles = candles_from_records(bars_5m[-200:])
    except Exception as exc:  # noqa: BLE001
        setup.rejection_reasons.append(f"5m: bar parse failed ({exc!r})")
        return setup
    if not candles:
        setup.rejection_reasons.append("5m: empty candles after parse")
        return setup

    # Long candidate
    long_setup = _try_long_5m(candles, sweep_lookback)
    short_setup = _try_short_5m(candles, sweep_lookback)

    candidates: list[FiveMinuteSetup] = []
    if direction_hint == "long":
        if long_setup.found:
            candidates.append(long_setup)
    elif direction_hint == "short":
        if short_setup.found:
            candidates.append(short_setup)
    else:  # auto
        for s in (long_setup, short_setup):
            if s.found:
                candidates.append(s)

    if not candidates:
        # Combine rejection reasons for visibility.
        reasons = (long_setup.rejection_reasons or []) + (
            short_setup.rejection_reasons or []
        )
        setup.rejection_reasons.extend(reasons or ["5m: no sweep+reclaim found"])
        return setup

    # Prefer the most recent sweep_index.
    candidates.sort(key=lambda s: (s.sweep_index or -1), reverse=True)
    return candidates[0]


def _try_long_5m(candles: list[Candle], sweep_lookback: int) -> FiveMinuteSetup:
    setup = FiveMinuteSetup(direction=DIRECTION_LONG)
    sweeps = detect_liquidity_sweep(candles, lookback=sweep_lookback)
    if not sweeps:
        setup.rejection_reasons.append("5m long: no sell-side sweep")
        return setup
    sweep = sweeps[-1]
    choch = detect_choch_after_sweep(candles, sweep, max_bars_after_sweep=10)
    fvgs = detect_bullish_fvg(candles)
    fvg = None
    if choch:
        sweep_idx = sweep["index"]
        choch_idx = choch["index"]
        for f in fvgs:
            mid = f["end_index"] - 1
            if sweep_idx < mid <= choch_idx + 3:
                fvg = f
                break
    ob = detect_bullish_order_block(candles, choch) if choch else None

    setup.found = True
    setup.sweep_index = int(sweep["index"])
    setup.sweep_timestamp = str(sweep.get("timestamp", ""))
    setup.swept_level_price = float(sweep.get("swept_low_price", 0.0) or 0.0)
    setup.reclaim_close = float(sweep.get("close", 0.0) or 0.0)
    setup.mss_found = bool(choch)
    setup.mss_pivot_price = (
        float(choch["pivot_high_broken"]) if choch and "pivot_high_broken" in choch else None
    )
    setup.has_fvg = bool(fvg)
    fl, fh = _zone_from_fvg(fvg)
    setup.fvg_low, setup.fvg_high = fl, fh
    setup.has_order_block = bool(ob)
    ol, oh = _zone_from_ob(ob)
    setup.order_block_low, setup.order_block_high = ol, oh
    setup.setup_kind = _setup_kind(setup.has_fvg, setup.has_order_block)
    # Setup zone: prefer FVG, else OB, else swept-low to reclaim-close band.
    if setup.has_fvg:
        setup.setup_zone_low, setup.setup_zone_high = fl, fh
    elif setup.has_order_block:
        setup.setup_zone_low, setup.setup_zone_high = ol, oh
    else:
        setup.setup_zone_low = setup.swept_level_price
        setup.setup_zone_high = max(setup.swept_level_price or 0.0, setup.reclaim_close or 0.0)
    return setup


def _try_short_5m(candles: list[Candle], sweep_lookback: int) -> FiveMinuteSetup:
    setup = FiveMinuteSetup(direction=DIRECTION_SHORT)
    sweeps = detect_bearish_liquidity_sweep(candles, lookback=sweep_lookback)
    if not sweeps:
        setup.rejection_reasons.append("5m short: no buy-side sweep")
        return setup
    sweep = sweeps[-1]
    choch = detect_bearish_choch_after_sweep(candles, sweep, max_bars_after_sweep=10)
    fvgs = detect_bearish_fvg(candles)
    fvg = None
    if choch:
        sweep_idx = sweep["index"]
        choch_idx = choch["index"]
        for f in fvgs:
            mid = f["end_index"] - 1
            if sweep_idx < mid <= choch_idx + 3:
                fvg = f
                break
    ob = detect_bearish_order_block(candles, choch) if choch else None

    setup.found = True
    setup.sweep_index = int(sweep["index"])
    setup.sweep_timestamp = str(sweep.get("timestamp", ""))
    setup.swept_level_price = float(sweep.get("swept_high_price", 0.0) or 0.0)
    setup.reclaim_close = float(sweep.get("close", 0.0) or 0.0)
    setup.mss_found = bool(choch)
    setup.mss_pivot_price = (
        float(choch["pivot_low_broken"]) if choch and "pivot_low_broken" in choch else None
    )
    setup.has_fvg = bool(fvg)
    fl, fh = _zone_from_fvg(fvg)
    setup.fvg_low, setup.fvg_high = fl, fh
    setup.has_order_block = bool(ob)
    ol, oh = _zone_from_ob(ob)
    setup.order_block_low, setup.order_block_high = ol, oh
    setup.setup_kind = _setup_kind(setup.has_fvg, setup.has_order_block)
    if setup.has_fvg:
        setup.setup_zone_low, setup.setup_zone_high = fl, fh
    elif setup.has_order_block:
        setup.setup_zone_low, setup.setup_zone_high = ol, oh
    else:
        setup.setup_zone_low = min(
            setup.swept_level_price or 0.0, setup.reclaim_close or 0.0
        )
        setup.setup_zone_high = setup.swept_level_price
    return setup


# ---------------------------------------------------------------------------
# 1m entry trigger
# ---------------------------------------------------------------------------
def _displacement_strong(candle: Candle, candles: list[Candle], i: int) -> bool:
    """Crude displacement check: body >= 1.5x recent average body, in trend dir."""
    if i < 5:
        return False
    avg = sum(abs(c.close - c.open) for c in candles[i - 5:i]) / 5.0
    body = abs(candle.close - candle.open)
    return avg > 0 and body >= 1.5 * avg


def _zone_overlaps_price(
    zone_low: float | None, zone_high: float | None, price: float
) -> bool:
    if zone_low is None or zone_high is None:
        return False
    return min(zone_low, zone_high) <= price <= max(zone_low, zone_high)


def detect_1m_entry_trigger(
    bars_1m: list[dict[str, Any]] | None,
    five_min_setup: FiveMinuteSetup,
    context: IntradayContext,
    *,
    sweep_lookback: int = 30,
) -> OneMinuteTrigger:
    """Detect the 1m sweep + MSS + entry source after price returns to setup zone."""
    trigger = OneMinuteTrigger(direction=five_min_setup.direction)
    if not five_min_setup.found:
        trigger.rejection_reasons.append("1m: 5m setup not found")
        return trigger
    if not bars_1m or len(bars_1m) < 6:
        trigger.rejection_reasons.append("1m: not enough bars")
        return trigger
    try:
        candles = candles_from_records(bars_1m[-400:])
    except Exception as exc:  # noqa: BLE001
        trigger.rejection_reasons.append(f"1m: bar parse failed ({exc!r})")
        return trigger
    if not candles:
        trigger.rejection_reasons.append("1m: empty candles after parse")
        return trigger

    # Did price recently visit the 5m setup zone?
    last_close = candles[-1].close
    in_zone = _zone_overlaps_price(
        five_min_setup.setup_zone_low,
        five_min_setup.setup_zone_high,
        last_close,
    )
    visited = any(
        _zone_overlaps_price(
            five_min_setup.setup_zone_low,
            five_min_setup.setup_zone_high,
            c.low if five_min_setup.direction == DIRECTION_LONG else c.high,
        )
        for c in candles[-60:]
    )
    if not (in_zone or visited):
        trigger.rejection_reasons.append("1m: price has not returned to setup zone")
        return trigger

    if five_min_setup.direction == DIRECTION_LONG:
        sweeps = detect_liquidity_sweep(candles, lookback=sweep_lookback)
        if not sweeps:
            trigger.rejection_reasons.append("1m long: no sell-side micro sweep")
            return trigger
        sweep = sweeps[-1]
        choch = detect_choch_after_sweep(candles, sweep, max_bars_after_sweep=8)
        if not choch:
            trigger.rejection_reasons.append("1m long: no MSS/ChoCH after micro sweep")
            trigger.sweep_index = int(sweep["index"])
            trigger.sweep_timestamp = str(sweep.get("timestamp", ""))
            trigger.swept_level_price = float(sweep.get("swept_low_price", 0.0) or 0.0)
            return trigger
        fvgs = detect_bullish_fvg(candles)
        fvg = None
        sweep_idx = sweep["index"]
        choch_idx = choch["index"]
        for f in fvgs:
            mid = f["end_index"] - 1
            if sweep_idx < mid <= choch_idx + 3:
                fvg = f
                break
        ob = detect_bullish_order_block(candles, choch)
        trigger.found = True
        trigger.sweep_index = int(sweep_idx)
        trigger.sweep_timestamp = str(sweep.get("timestamp", ""))
        trigger.swept_level_price = float(sweep.get("swept_low_price", 0.0) or 0.0)
        trigger.mss_found = True
        trigger.mss_pivot_price = float(choch["pivot_high_broken"])
        if fvg:
            trigger.entry_source = ENTRY_SOURCE_FVG
            trigger.fvg_low = float(fvg["low"])
            trigger.fvg_high = float(fvg["high"])
        elif ob:
            trigger.entry_source = ENTRY_SOURCE_OB
            trigger.ob_low = float(ob["low"])
            trigger.ob_high = float(ob["high"])
        else:
            trigger.entry_source = ENTRY_SOURCE_BREAKER
        trigger.has_displacement = _displacement_strong(
            candles[choch_idx], candles, choch_idx
        )
        return trigger

    # short
    sweeps = detect_bearish_liquidity_sweep(candles, lookback=sweep_lookback)
    if not sweeps:
        trigger.rejection_reasons.append("1m short: no buy-side micro sweep")
        return trigger
    sweep = sweeps[-1]
    choch = detect_bearish_choch_after_sweep(candles, sweep, max_bars_after_sweep=8)
    if not choch:
        trigger.rejection_reasons.append("1m short: no MSS/ChoCH after micro sweep")
        trigger.sweep_index = int(sweep["index"])
        trigger.sweep_timestamp = str(sweep.get("timestamp", ""))
        trigger.swept_level_price = float(sweep.get("swept_high_price", 0.0) or 0.0)
        return trigger
    fvgs = detect_bearish_fvg(candles)
    fvg = None
    sweep_idx = sweep["index"]
    choch_idx = choch["index"]
    for f in fvgs:
        mid = f["end_index"] - 1
        if sweep_idx < mid <= choch_idx + 3:
            fvg = f
            break
    ob = detect_bearish_order_block(candles, choch)
    trigger.found = True
    trigger.sweep_index = int(sweep_idx)
    trigger.sweep_timestamp = str(sweep.get("timestamp", ""))
    trigger.swept_level_price = float(sweep.get("swept_high_price", 0.0) or 0.0)
    trigger.mss_found = True
    trigger.mss_pivot_price = float(choch["pivot_low_broken"])
    if fvg:
        trigger.entry_source = ENTRY_SOURCE_FVG
        trigger.fvg_low = float(fvg["low"])
        trigger.fvg_high = float(fvg["high"])
    elif ob:
        trigger.entry_source = ENTRY_SOURCE_OB
        trigger.ob_low = float(ob["low"])
        trigger.ob_high = float(ob["high"])
    else:
        trigger.entry_source = ENTRY_SOURCE_BREAKER
    trigger.has_displacement = _displacement_strong(
        candles[choch_idx], candles, choch_idx
    )
    return trigger


__all__ = [
    "build_intraday_context",
    "detect_5m_setup",
    "detect_1m_entry_trigger",
    "detect_bearish_choch_after_sweep",
    "detect_bearish_fvg",
    "detect_bearish_liquidity_sweep",
    "detect_bearish_order_block",
]
