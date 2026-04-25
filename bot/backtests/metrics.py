"""Metric aggregation for the intraday backtest (Prompt 13E PART B).

Pure functions on a sequence of :class:`Trade` objects. NO imports
from the broker, IBKR client, or matplotlib. The simulator
(``intraday_engine.backtest_intraday_smc``) calls
:func:`compute_metrics` once at the end of a run; the report layer
serialises the result to JSON / Markdown.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:  # avoid runtime cycle
    from .intraday_engine import Trade


SIGNAL_STRICT = "DAY_TRADE_READY_STRICT"
SIGNAL_AGGRESSIVE = "DAY_TRADE_READY_AGGRESSIVE"


@dataclass(frozen=True)
class SymbolBreakdown:
    """Per-symbol metrics row used by the JSON / Markdown report."""

    symbol: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    average_r: float | None = None
    total_r: float = 0.0


@dataclass
class BacktestMetrics:
    """Top-level metric block returned by :func:`compute_metrics`."""

    total_signals: int = 0
    total_filled_trades: int = 0
    total_not_filled: int = 0
    win_rate: float | None = None
    average_r: float | None = None
    median_r: float | None = None
    total_r: float = 0.0
    max_drawdown_r: float = 0.0
    profit_factor: float | None = None
    average_bars_held: float | None = None
    strict_count: int = 0
    aggressive_count: int = 0
    strict_win_rate: float | None = None
    aggressive_win_rate: float | None = None
    long_win_rate: float | None = None
    short_win_rate: float | None = None
    by_symbol: list[SymbolBreakdown] = field(default_factory=list)
    by_hour: dict[str, dict[str, float]] = field(default_factory=dict)
    by_weekday: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_signals": self.total_signals,
            "total_filled_trades": self.total_filled_trades,
            "total_not_filled": self.total_not_filled,
            "win_rate": _round(self.win_rate),
            "average_r": _round(self.average_r),
            "median_r": _round(self.median_r),
            "total_r": _round(self.total_r),
            "max_drawdown_r": _round(self.max_drawdown_r),
            "profit_factor": _round(self.profit_factor),
            "average_bars_held": _round(self.average_bars_held),
            "strict_count": self.strict_count,
            "aggressive_count": self.aggressive_count,
            "strict_win_rate": _round(self.strict_win_rate),
            "aggressive_win_rate": _round(self.aggressive_win_rate),
            "long_win_rate": _round(self.long_win_rate),
            "short_win_rate": _round(self.short_win_rate),
            "by_symbol": [
                {
                    "symbol": b.symbol,
                    "trades": b.trades,
                    "wins": b.wins,
                    "losses": b.losses,
                    "win_rate": _round(b.win_rate),
                    "average_r": _round(b.average_r),
                    "total_r": _round(b.total_r),
                }
                for b in self.by_symbol
            ],
            "by_hour": _round_breakdown(self.by_hour),
            "by_weekday": _round_breakdown(self.by_weekday),
        }


def _round(v: float | None, ndigits: int = 4) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _round_breakdown(d: Mapping[str, Mapping[str, float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for k, v in d.items():
        out[str(k)] = {kk: _round(vv) for kk, vv in v.items()}
    return out


def _safe_win_rate(filled: list["Trade"]) -> float | None:
    if not filled:
        return None
    wins = sum(1 for t in filled if t.outcome == "win")
    return wins / len(filled)


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _safe_median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _max_drawdown_r(filled: list["Trade"]) -> float:
    """Return the max drop from peak in cumulative R (negative)."""
    cum = 0.0
    peak = 0.0
    worst = 0.0
    for t in filled:
        r = t.pnl_r if t.pnl_r is not None else 0.0
        cum += r
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def _profit_factor(filled: list["Trade"]) -> float | None:
    gains = sum(t.pnl_r for t in filled if (t.pnl_r or 0) > 0)
    losses = sum(-t.pnl_r for t in filled if (t.pnl_r or 0) < 0)
    if losses <= 0:
        if gains <= 0:
            return None
        return float("inf")
    return gains / losses


def _bucket_key_hour(t: "Trade") -> str | None:
    if not t.entry_time:
        return None
    try:
        dt = _parse(t.entry_time)
    except ValueError:
        return None
    return f"{dt.hour:02d}:00"


def _bucket_key_weekday(t: "Trade") -> str | None:
    if not t.entry_time:
        return None
    try:
        dt = _parse(t.entry_time)
    except ValueError:
        return None
    names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return names[dt.weekday()]


def _parse(ts: str) -> datetime:
    """Parse one of the timestamp shapes IBKR returns."""
    s = ts.replace("T", " ").strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Strip trailing TZ name if present.
        for suffix in (" US/Eastern", " EST", " EDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        return datetime.fromisoformat(s)


def _bucket_breakdown(
    filled: list["Trade"], key_fn
) -> dict[str, dict[str, float]]:
    by: dict[str, list[float]] = defaultdict(list)
    counts: Counter = Counter()
    wins: Counter = Counter()
    for t in filled:
        k = key_fn(t)
        if k is None:
            continue
        counts[k] += 1
        if t.pnl_r is not None:
            by[k].append(float(t.pnl_r))
        if t.outcome == "win":
            wins[k] += 1
    out: dict[str, dict[str, float]] = {}
    for k, n in counts.items():
        rs = by.get(k, [])
        out[k] = {
            "trades": float(n),
            "wins": float(wins.get(k, 0)),
            "win_rate": (wins[k] / n) if n else None,
            "total_r": sum(rs),
            "average_r": (sum(rs) / len(rs)) if rs else None,
        }
    return dict(sorted(out.items()))


def compute_metrics(
    trades: Iterable["Trade"],
    *,
    total_signals: int | None = None,
) -> BacktestMetrics:
    """Aggregate ``trades`` into a :class:`BacktestMetrics` block."""
    all_trades = list(trades)
    filled = [t for t in all_trades if t.outcome in {"win", "loss", "eod_exit"}]
    not_filled = [t for t in all_trades if t.outcome == "not_filled"]

    rs = [float(t.pnl_r) for t in filled if t.pnl_r is not None]
    bars_held = [t.bars_held for t in filled if t.bars_held is not None]

    strict = [t for t in filled if t.signal_category == SIGNAL_STRICT]
    aggressive = [t for t in filled if t.signal_category == SIGNAL_AGGRESSIVE]
    longs = [t for t in filled if t.direction == "long"]
    shorts = [t for t in filled if t.direction == "short"]

    by_symbol_map: dict[str, list[Trade]] = defaultdict(list)
    for t in filled:
        by_symbol_map[t.symbol].append(t)

    by_symbol_rows: list[SymbolBreakdown] = []
    for sym, ts in sorted(by_symbol_map.items()):
        wins = sum(1 for x in ts if x.outcome == "win")
        rs_sym = [float(x.pnl_r) for x in ts if x.pnl_r is not None]
        by_symbol_rows.append(
            SymbolBreakdown(
                symbol=sym,
                trades=len(ts),
                wins=wins,
                losses=len(ts) - wins,
                win_rate=_safe_win_rate(ts),
                average_r=_safe_mean(rs_sym),
                total_r=float(sum(rs_sym)),
            )
        )

    return BacktestMetrics(
        total_signals=int(total_signals if total_signals is not None else len(all_trades)),
        total_filled_trades=len(filled),
        total_not_filled=len(not_filled),
        win_rate=_safe_win_rate(filled),
        average_r=_safe_mean(rs),
        median_r=_safe_median(rs),
        total_r=float(sum(rs)),
        max_drawdown_r=_max_drawdown_r(filled),
        profit_factor=_profit_factor(filled),
        average_bars_held=_safe_mean([float(b) for b in bars_held]) if bars_held else None,
        strict_count=len(strict),
        aggressive_count=len(aggressive),
        strict_win_rate=_safe_win_rate(strict),
        aggressive_win_rate=_safe_win_rate(aggressive),
        long_win_rate=_safe_win_rate(longs),
        short_win_rate=_safe_win_rate(shorts),
        by_symbol=by_symbol_rows,
        by_hour=_bucket_breakdown(filled, _bucket_key_hour),
        by_weekday=_bucket_breakdown(filled, _bucket_key_weekday),
    )


__all__ = [
    "BacktestMetrics",
    "SymbolBreakdown",
    "compute_metrics",
]
