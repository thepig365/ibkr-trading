"""Lightweight ICT-style probe on 1m FX bars (research-only heuristic)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from bot.backtests.candle_cache import BarRow


@dataclass
class FxIctSignal:
    pair: str
    direction: str  # long | short | flat
    setup_type: str
    entry: float | None
    stop: float | None
    target: float | None
    rr: float | None
    liquidity_sweep: bool
    displacement: bool
    fvg: bool
    order_block: bool
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def simple_fx_ict_scan(pair_display: str, bars: list[BarRow]) -> FxIctSignal | None:
    """Return a single directional proposal or None if insufficient data."""

    if len(bars) < 12:
        return None
    tail = bars[-12:]
    lows = [x.low for x in tail]
    highs = [x.high for x in tail]
    last = tail[-1]

    swing_low = min(lows[:-1])
    swing_high = max(highs[:-1])

    bullish_sweep = last.low < swing_low - 1e-12 and last.close > tail[-2].close
    bearish_sweep = last.high > swing_high + 1e-12 and last.close < tail[-2].close

    atr = max(h - l for h, l in zip(highs, lows, strict=False)) or 1e-9

    if bullish_sweep:
        entry = float(last.close)
        stop = float(last.low) - 0.2 * atr
        target = float(last.close) + 1.2 * atr
        if target <= entry or entry <= stop:
            return None
        rr = abs((target - entry) / max(entry - stop, 1e-12))
        return FxIctSignal(
            pair=pair_display,
            direction="long",
            setup_type="liquidity_sweep_long",
            entry=entry,
            stop=stop,
            target=target,
            rr=float(rr),
            liquidity_sweep=True,
            displacement=last.close > tail[-3].high,
            fvg=False,
            order_block=False,
            confidence=0.42,
            reason="Sweep below prior micro-range + bullish close (test heuristic).",
        )
    if bearish_sweep:
        entry = float(last.close)
        stop = float(last.high) + 0.2 * atr
        target = float(last.close) - 1.2 * atr
        if target >= entry or entry >= stop:
            return None
        rr = abs((entry - target) / max(stop - entry, 1e-12))
        return FxIctSignal(
            pair=pair_display,
            direction="short",
            setup_type="liquidity_sweep_short",
            entry=entry,
            stop=stop,
            target=target,
            rr=float(rr),
            liquidity_sweep=True,
            displacement=last.close < tail[-3].low,
            fvg=False,
            order_block=False,
            confidence=0.40,
            reason="Sweep above prior micro-range + bearish close (test heuristic).",
        )
    return FxIctSignal(
        pair=pair_display,
        direction="flat",
        setup_type="none",
        entry=None,
        stop=None,
        target=None,
        rr=None,
        liquidity_sweep=False,
        displacement=False,
        fvg=False,
        order_block=False,
        confidence=0.05,
        reason="No sweep signal on last bars (test heuristic).",
    )


__all__ = ["FxIctSignal", "simple_fx_ict_scan"]
