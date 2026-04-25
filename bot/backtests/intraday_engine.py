"""No-lookahead intraday backtest engine for ICT/SMC Liquidity Reversal V1.

Reads 1m bars from :mod:`bot.backtests.candle_cache`, resamples to
5m/30m/4H (no lookahead — only completed higher-TF bars are visible to
the strategy at each 1m step), runs
:func:`bot.strategies.ict_smc_intraday.scan_symbol_from_bars` on the
completed slice, and simulates a limit bracket order with realistic
fill / stop-first-on-tie rules.

Hard invariants
---------------
* NO order placement. NO broker import. NO IBKR connection. The
  engine only consumes CSV-cached candles.
* Every payload carries ``execution_allowed=False`` and
  ``paper_only=True``.
* Same-symbol overlapping positions are forbidden by default —
  one open position per symbol at a time.
* Entries are skipped before 09:45 ET and after 15:30 ET.
* Open positions are flat-closed at 15:55 ET (configurable).
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..strategies.ict_smc_intraday import (
    IntradayRiskConfig,
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
    scan_symbol_from_bars,
)
from .candle_cache import BarRow, load_candles
from .metrics import BacktestMetrics, compute_metrics

LOG = logging.getLogger(__name__)

BACKTEST_STRATEGY_KEY = "ict_smc_intraday_v1"

# Entry window (ET) — entries only between these wall-clock times.
ENTRY_OPEN_TIME = time(9, 45)
ENTRY_CLOSE_TIME = time(15, 30)
EOD_FORCE_FLAT_TIME = time(15, 55)
RTH_OPEN_TIME = time(9, 30)
RTH_CLOSE_TIME = time(16, 0)

# Mode aliases.
MODE_STRICT_ONLY = "strict_only"
MODE_AGGRESSIVE_ONLY = "aggressive_only"
MODE_BOTH = "strict_and_aggressive"
ALLOWED_MODES = frozenset({MODE_STRICT_ONLY, MODE_AGGRESSIVE_ONLY, MODE_BOTH})

# Direction aliases.
DIRECTION_LONG_ONLY = "long_only"
DIRECTION_SHORT_ONLY = "short_only"
DIRECTION_BOTH = "both"
ALLOWED_DIRECTIONS = frozenset({DIRECTION_LONG_ONLY, DIRECTION_SHORT_ONLY, DIRECTION_BOTH})


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    """Inputs for one backtest run."""

    symbols: tuple[str, ...]
    start: str  # YYYY-MM-DD inclusive
    end: str    # YYYY-MM-DD inclusive
    mode: str = MODE_BOTH
    direction: str = DIRECTION_BOTH
    rth_only: bool = True
    risk_cfg: IntradayRiskConfig = field(default_factory=IntradayRiskConfig)
    # Limit-order pending lifetime (in 1m bars). After this many bars the
    # pending order expires as ``not_filled``.
    pending_lifetime_bars: int = 30
    # Allow a fresh signal once an open position closes within the same day.
    allow_multiple_trades_per_day: bool = True
    # Strategy bias hint passed to the detector (auto/long/short).
    direction_hint: str = "auto"
    paper_only: bool = True
    execution_allowed: bool = False  # hard invariant
    extras: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.execution_allowed:
            raise ValueError("BacktestConfig.execution_allowed must be False")
        if not self.paper_only:
            raise ValueError("BacktestConfig.paper_only must be True")
        if self.mode not in ALLOWED_MODES:
            raise ValueError(
                f"BacktestConfig.mode={self.mode!r} not in {sorted(ALLOWED_MODES)}"
            )
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(
                f"BacktestConfig.direction={self.direction!r} not in {sorted(ALLOWED_DIRECTIONS)}"
            )
        # Normalise / validate symbols.
        clean: list[str] = []
        for s in self.symbols:
            su = (s or "").strip().upper()
            if not su:
                continue
            clean.append(su)
        object.__setattr__(self, "symbols", tuple(clean))


@dataclass
class Trade:
    """Single simulated trade entry (fields per Prompt 13E spec)."""

    trade_id: str
    symbol: str
    date: str
    strategy_id: str
    direction: str
    signal_category: str
    setup_type: str = ""
    trigger_type: str = ""
    entry_time: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    exit_time: str = ""
    exit_price: float | None = None
    outcome: str = ""  # not_filled | win | loss | eod_exit
    pnl_r: float | None = None
    gross_pnl: float | None = None
    planned_rr: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    bars_held: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "date": self.date,
            "strategy_id": self.strategy_id,
            "direction": self.direction,
            "signal_category": self.signal_category,
            "setup_type": self.setup_type,
            "trigger_type": self.trigger_type,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "outcome": self.outcome,
            "pnl_r": _round(self.pnl_r),
            "gross_pnl": _round(self.gross_pnl),
            "planned_rr": _round(self.planned_rr),
            "mfe_r": _round(self.mfe_r),
            "mae_r": _round(self.mae_r),
            "bars_held": self.bars_held,
            "notes": list(self.notes),
        }


@dataclass
class BacktestRun:
    """Top-level result returned by :func:`backtest_intraday_smc`."""

    cfg: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    started_at_utc: str = ""
    finished_at_utc: str = ""
    paper_only: bool = True
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": BACKTEST_STRATEGY_KEY,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "config": {
                "symbols": list(self.cfg.symbols),
                "start": self.cfg.start,
                "end": self.cfg.end,
                "mode": self.cfg.mode,
                "direction": self.cfg.direction,
                "rth_only": self.cfg.rth_only,
                "pending_lifetime_bars": self.cfg.pending_lifetime_bars,
                "allow_multiple_trades_per_day": self.cfg.allow_multiple_trades_per_day,
            },
            "metrics": self.metrics.to_dict(),
            "trades": [t.to_dict() for t in self.trades],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------
def _parse_dt(ts: str) -> datetime | None:
    """Parse a candle timestamp; return None if unparseable."""
    if not ts:
        return None
    s = ts.replace("T", " ").strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for suffix in (" US/Eastern", " EST", " EDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    # Drop tz; we only need wall-clock comparisons.
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _floor_to_window(dt: datetime, minutes: int) -> datetime:
    """Floor ``dt`` to the start of an N-minute window aligned at midnight."""
    base_minutes = dt.hour * 60 + dt.minute
    floored = (base_minutes // minutes) * minutes
    new_h, new_m = divmod(floored, 60)
    return dt.replace(hour=new_h, minute=new_m, second=0, microsecond=0)


def resample_bars(
    bars_1m: Sequence[BarRow], minutes: int
) -> list[dict[str, Any]]:
    """Resample 1m bars into N-minute aggregated bars.

    Aligned to wall-clock midnight (so 5m bars start at :00, :05, ...,
    30m bars at :00, :30, 4H at 00:00, 04:00, 08:00, 12:00). Each
    aggregated bar uses the start timestamp of its first underlying
    1m bar, the standard high/low aggregation, the close of the last
    1m bar in the window, and summed volume.

    The function does NOT emit a partial bar — only fully-completed
    windows are returned. Callers can append in-progress bars from the
    streaming loop separately if needed (the engine intentionally does
    not, since 'no-lookahead' means we hide the in-progress higher-TF
    bar from the detector).
    """
    if minutes <= 0 or not bars_1m:
        return []
    buckets: dict[datetime, list[BarRow]] = defaultdict(list)
    for b in bars_1m:
        dt = _parse_dt(b.timestamp)
        if dt is None:
            continue
        key = _floor_to_window(dt, minutes)
        buckets[key].append(b)
    out: list[dict[str, Any]] = []
    for start, group in sorted(buckets.items()):
        if not group:
            continue
        # Group must contain >= ``minutes`` 1m bars to be a *complete*
        # higher-TF bar. With RTH-only data this ignores partial
        # market-edge windows (e.g. 09:30-09:34 sometimes only has 4
        # bars after a halt). For 5m we soften this to >= 1 since
        # session-open 5m bars are valid even when only one 1m bar
        # printed before halt.
        if minutes <= 5 or len(group) >= minutes:
            opener = group[0]
            closer = group[-1]
            high = max(g.high for g in group)
            low = min(g.low for g in group if g.low > 0) if any(g.low > 0 for g in group) else opener.low
            volume = sum(g.volume for g in group)
            out.append(
                {
                    "timestamp": opener.timestamp,
                    "open": opener.open,
                    "high": high,
                    "low": low,
                    "close": closer.close,
                    "volume": volume,
                    "_window_minutes": minutes,
                    "_bar_count": len(group),
                }
            )
    return out


def _split_by_day(bars_1m: Sequence[BarRow]) -> list[tuple[str, list[BarRow]]]:
    """Group 1m bars by ``YYYY-MM-DD`` (using their parsed datetime)."""
    by_day: dict[str, list[BarRow]] = defaultdict(list)
    for b in bars_1m:
        dt = _parse_dt(b.timestamp)
        if dt is None:
            continue
        by_day[dt.date().isoformat()].append(b)
    out: list[tuple[str, list[BarRow]]] = []
    for day in sorted(by_day):
        rows = sorted(by_day[day], key=lambda r: r.timestamp)
        out.append((day, rows))
    return out


# ---------------------------------------------------------------------------
# Trade simulation helpers
# ---------------------------------------------------------------------------
@dataclass
class _PendingOrder:
    direction: str
    entry: float
    stop: float
    target: float
    signal_category: str
    setup_type: str
    trigger_type: str
    bars_alive: int = 0
    placed_at_dt: datetime | None = None
    placed_at_ts: str = ""
    planned_rr: float | None = None


@dataclass
class _OpenPosition:
    direction: str
    entry: float
    stop: float
    target: float
    signal_category: str
    setup_type: str
    trigger_type: str
    entered_at_dt: datetime | None = None
    entered_at_ts: str = ""
    bars_held: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    planned_rr: float | None = None


def _round(v: float | None, ndigits: int = 4) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _signal_passes_filters(
    signal_category: str, direction: str, cfg: BacktestConfig
) -> bool:
    if signal_category == SIGNAL_DAY_TRADE_READY_STRICT:
        if cfg.mode == MODE_AGGRESSIVE_ONLY:
            return False
    elif signal_category == SIGNAL_DAY_TRADE_READY_AGGRESSIVE:
        if cfg.mode == MODE_STRICT_ONLY:
            return False
    else:
        return False
    if direction == "long" and cfg.direction == DIRECTION_SHORT_ONLY:
        return False
    if direction == "short" and cfg.direction == DIRECTION_LONG_ONLY:
        return False
    return True


def _bar_in_entry_window(dt: datetime) -> bool:
    return ENTRY_OPEN_TIME <= dt.time() <= ENTRY_CLOSE_TIME


def _bar_at_or_after_eod(dt: datetime) -> bool:
    return dt.time() >= EOD_FORCE_FLAT_TIME


def _bar_in_rth(dt: datetime) -> bool:
    return RTH_OPEN_TIME <= dt.time() < RTH_CLOSE_TIME


# ---------------------------------------------------------------------------
# Per-symbol simulation
# ---------------------------------------------------------------------------
def _simulate_symbol(
    symbol: str,
    bars_1m: Sequence[BarRow],
    cfg: BacktestConfig,
    *,
    notes_out: list[str] | None = None,
) -> tuple[list[Trade], int]:
    """Run the no-lookahead simulation for a single symbol.

    Returns ``(trades, total_signals_emitted)``.
    """
    if not bars_1m:
        return [], 0

    notes_out = notes_out if notes_out is not None else []

    # Pre-compute parsed datetimes once.
    parsed = [(b, _parse_dt(b.timestamp)) for b in bars_1m]
    parsed = [(b, dt) for b, dt in parsed if dt is not None]
    if not parsed:
        notes_out.append(f"{symbol}: no parseable timestamps in 1m bars.")
        return [], 0

    # Pre-resample full history to 5m / 30m / 4h. The slicer below
    # keeps only completed bars whose end <= current 1m bar end.
    bars_5m_full = resample_bars(bars_1m, 5)
    bars_30m_full = resample_bars(bars_1m, 30)
    bars_4h_full = resample_bars(bars_1m, 240)

    # Pre-compute end_dt for each resampled bar.
    def _annotate(bars: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for b in bars:
            dt = _parse_dt(b["timestamp"])
            if dt is None:
                continue
            b2 = dict(b)
            b2["_end_dt"] = dt + timedelta(minutes=minutes)
            out.append(b2)
        return out

    bars_5m_full = _annotate(bars_5m_full, 5)
    bars_30m_full = _annotate(bars_30m_full, 30)
    bars_4h_full = _annotate(bars_4h_full, 240)

    days = _split_by_day(bars_1m)
    trades: list[Trade] = []
    total_signals = 0
    last_eval_5m_count = -1  # cache: avoid re-running scan when nothing changed

    for day, day_bars_1m in days:
        if cfg.start and day < cfg.start:
            continue
        if cfg.end and day > cfg.end:
            continue
        # Build a rolling 1m history that grows day-by-day for the
        # 4H/30m context. We pass the *full* 1m history up to "now" so
        # the detector has enough bars (5m setup needs 100+ bars,
        # which spans ~2 days).
        # Walk the day's 1m bars in order.
        pending: _PendingOrder | None = None
        position: _OpenPosition | None = None
        any_trade_this_day = False

        for raw_bar in day_bars_1m:
            cur_dt = _parse_dt(raw_bar.timestamp)
            if cur_dt is None:
                continue
            if cfg.rth_only and not _bar_in_rth(cur_dt):
                continue
            cur_end_dt = cur_dt + timedelta(minutes=1)

            # ----- Position management on this 1m bar -----
            if position is not None:
                _step_position(position, raw_bar, cur_end_dt, trades, symbol, day, cfg)
                # ``_step_position`` flips ``bars_held`` to a negative
                # sentinel after writing the closing trade; that's our
                # signal to release the slot for the rest of the day.
                if _check_position_closed(trades, position):
                    position = None
                    any_trade_this_day = True

            # EOD flat-close.
            if position is not None and _bar_at_or_after_eod(cur_dt):
                _close_eod(position, raw_bar, cur_end_dt, trades, symbol, day)
                position = None
                any_trade_this_day = True

            # ----- Pending limit fill on this 1m bar -----
            if pending is not None and position is None:
                fill = _try_fill(pending, raw_bar)
                if fill is not None:
                    position = _OpenPosition(
                        direction=pending.direction,
                        entry=pending.entry,
                        stop=pending.stop,
                        target=pending.target,
                        signal_category=pending.signal_category,
                        setup_type=pending.setup_type,
                        trigger_type=pending.trigger_type,
                        entered_at_dt=cur_dt,
                        entered_at_ts=raw_bar.timestamp,
                        planned_rr=pending.planned_rr,
                    )
                    pending = None
                else:
                    pending.bars_alive += 1
                    if pending.bars_alive >= cfg.pending_lifetime_bars or _bar_at_or_after_eod(cur_dt):
                        # Expire as not_filled.
                        trades.append(
                            Trade(
                                trade_id=str(uuid.uuid4())[:8],
                                symbol=symbol,
                                date=day,
                                strategy_id=BACKTEST_STRATEGY_KEY,
                                direction=pending.direction,
                                signal_category=pending.signal_category,
                                setup_type=pending.setup_type,
                                trigger_type=pending.trigger_type,
                                entry_time=pending.placed_at_ts,
                                entry_price=pending.entry,
                                stop_price=pending.stop,
                                target_price=pending.target,
                                exit_time=raw_bar.timestamp,
                                outcome="not_filled",
                                planned_rr=pending.planned_rr,
                                pnl_r=0.0,
                                notes=["pending order expired"],
                            )
                        )
                        pending = None
                        any_trade_this_day = True

            # ----- Signal generation (no-lookahead) -----
            already_traded = (
                any_trade_this_day and not cfg.allow_multiple_trades_per_day
            )
            if (
                pending is None
                and position is None
                and not already_traded
                and _bar_in_entry_window(cur_dt)
            ):
                bars_4h_done = [
                    b for b in bars_4h_full if b["_end_dt"] <= cur_end_dt
                ]
                bars_30m_done = [
                    b for b in bars_30m_full if b["_end_dt"] <= cur_end_dt
                ]
                bars_5m_done = [
                    b for b in bars_5m_full if b["_end_dt"] <= cur_end_dt
                ]
                bars_1m_done = [
                    {
                        "timestamp": b.timestamp,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                    }
                    for b in bars_1m
                    if (_parse_dt(b.timestamp) or cur_dt) <= cur_dt
                ]
                # Cheap guard: if 5m count hasn't changed since last eval
                # AND no new 1m bar (we always have a new 1m bar here),
                # we still re-run because the trigger is a 1m signal.
                # The 5m_count cache below is informational only.
                last_eval_5m_count = len(bars_5m_done)

                eval_obj = scan_symbol_from_bars(
                    symbol,
                    bars_4h=bars_4h_done,
                    bars_30m=bars_30m_done,
                    bars_5m=bars_5m_done,
                    bars_1m=bars_1m_done,
                    risk_cfg=cfg.risk_cfg,
                    direction_hint=cfg.direction_hint,
                    data_source="cache",
                )
                cat = eval_obj.signal_category
                direction = eval_obj.direction
                if not _signal_passes_filters(cat, direction, cfg):
                    continue
                plan = eval_obj.trade_plan
                trig = eval_obj.one_min_trigger
                setup = eval_obj.five_min_setup
                if plan is None or not plan.valid:
                    continue
                pending = _PendingOrder(
                    direction=direction,
                    entry=float(plan.entry),
                    stop=float(plan.stop),
                    target=float(plan.target),
                    signal_category=cat,
                    setup_type=str(setup.setup_kind) if setup else "",
                    trigger_type=str(trig.entry_source) if trig else "",
                    placed_at_dt=cur_dt,
                    placed_at_ts=raw_bar.timestamp,
                    planned_rr=plan.risk_reward,
                )
                total_signals += 1

        # End of day: pending orders that haven't filled expire.
        if pending is not None:
            trades.append(
                Trade(
                    trade_id=str(uuid.uuid4())[:8],
                    symbol=symbol,
                    date=day,
                    strategy_id=BACKTEST_STRATEGY_KEY,
                    direction=pending.direction,
                    signal_category=pending.signal_category,
                    setup_type=pending.setup_type,
                    trigger_type=pending.trigger_type,
                    entry_time=pending.placed_at_ts,
                    entry_price=pending.entry,
                    stop_price=pending.stop,
                    target_price=pending.target,
                    exit_time=day_bars_1m[-1].timestamp,
                    outcome="not_filled",
                    planned_rr=pending.planned_rr,
                    pnl_r=0.0,
                    notes=["pending order expired at session close"],
                )
            )
        if position is not None:
            _close_eod(position, day_bars_1m[-1], None, trades, symbol, day)
    return trades, total_signals


def _try_fill(p: _PendingOrder, bar: BarRow) -> bool | None:
    """Decide whether the pending limit bracket fills inside this 1m bar.

    Long: fill when bar.low <= entry <= bar.high (price traded
    *through* the limit, conservative).
    Short: fill when bar.low <= entry <= bar.high (same condition —
    a sell-limit fills when price trades up to the limit).

    Returns ``True`` on fill, ``None`` otherwise.
    """
    if p.direction == "long":
        if bar.low <= p.entry <= bar.high:
            return True
    elif p.direction == "short":
        if bar.low <= p.entry <= bar.high:
            return True
    return None


def _step_position(
    pos: _OpenPosition,
    bar: BarRow,
    cur_end_dt: datetime,
    trades: list[Trade],
    symbol: str,
    day: str,
    cfg: BacktestConfig,
) -> None:
    """Advance the open position by one 1m bar; record exit if hit."""
    pos.bars_held += 1
    risk = abs(pos.entry - pos.stop)
    if risk <= 0:
        return

    if pos.direction == "long":
        # Update MFE/MAE
        if bar.high > 0:
            r_high = (bar.high - pos.entry) / risk
            pos.mfe_r = max(pos.mfe_r, r_high)
        r_low = (bar.low - pos.entry) / risk
        pos.mae_r = min(pos.mae_r, r_low)
        hit_stop = bar.low <= pos.stop
        hit_target = bar.high >= pos.target
    else:
        r_low = (pos.entry - bar.low) / risk
        pos.mfe_r = max(pos.mfe_r, r_low)
        r_high = (pos.entry - bar.high) / risk
        pos.mae_r = min(pos.mae_r, r_high)
        hit_stop = bar.high >= pos.stop
        hit_target = bar.low <= pos.target

    if hit_stop and hit_target:
        # Stop-first conservative assumption.
        _close_position(pos, bar, "loss", pos.stop, trades, symbol, day)
        return
    if hit_stop:
        _close_position(pos, bar, "loss", pos.stop, trades, symbol, day)
        return
    if hit_target:
        _close_position(pos, bar, "win", pos.target, trades, symbol, day)
        return


def _close_position(
    pos: _OpenPosition,
    bar: BarRow,
    outcome: str,
    exit_price: float,
    trades: list[Trade],
    symbol: str,
    day: str,
) -> None:
    risk = abs(pos.entry - pos.stop) or 1.0
    if pos.direction == "long":
        pnl = exit_price - pos.entry
    else:
        pnl = pos.entry - exit_price
    pnl_r = pnl / risk
    trades.append(
        Trade(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            date=day,
            strategy_id=BACKTEST_STRATEGY_KEY,
            direction=pos.direction,
            signal_category=pos.signal_category,
            setup_type=pos.setup_type,
            trigger_type=pos.trigger_type,
            entry_time=pos.entered_at_ts,
            entry_price=pos.entry,
            stop_price=pos.stop,
            target_price=pos.target,
            exit_time=bar.timestamp,
            exit_price=exit_price,
            outcome=outcome,
            pnl_r=pnl_r,
            gross_pnl=pnl,
            planned_rr=pos.planned_rr,
            mfe_r=pos.mfe_r,
            mae_r=pos.mae_r,
            bars_held=pos.bars_held,
            notes=[],
        )
    )
    # Sentinel: mark pos closed by setting bars_held to a value the caller checks.
    pos.bars_held = -abs(pos.bars_held) - 1


def _check_position_closed(trades: list[Trade], pos: _OpenPosition) -> bool:
    return pos.bars_held < 0


def _close_eod(
    pos: _OpenPosition,
    bar: BarRow,
    cur_end_dt: datetime | None,
    trades: list[Trade],
    symbol: str,
    day: str,
) -> None:
    risk = abs(pos.entry - pos.stop) or 1.0
    exit_price = bar.close
    if pos.direction == "long":
        pnl = exit_price - pos.entry
    else:
        pnl = pos.entry - exit_price
    pnl_r = pnl / risk
    trades.append(
        Trade(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            date=day,
            strategy_id=BACKTEST_STRATEGY_KEY,
            direction=pos.direction,
            signal_category=pos.signal_category,
            setup_type=pos.setup_type,
            trigger_type=pos.trigger_type,
            entry_time=pos.entered_at_ts,
            entry_price=pos.entry,
            stop_price=pos.stop,
            target_price=pos.target,
            exit_time=bar.timestamp,
            exit_price=exit_price,
            outcome="eod_exit",
            pnl_r=pnl_r,
            gross_pnl=pnl,
            planned_rr=pos.planned_rr,
            mfe_r=pos.mfe_r,
            mae_r=pos.mae_r,
            bars_held=pos.bars_held,
            notes=["EOD force-flat"],
        )
    )
    # Mark the position closed using the same sentinel as
    # ``_close_position`` so the caller's ``_check_position_closed``
    # gate releases the slot.
    pos.bars_held = -abs(pos.bars_held) - 1


# ---------------------------------------------------------------------------
# Public driver
# ---------------------------------------------------------------------------
def _equity_curve(trades: list[Trade]) -> list[dict[str, Any]]:
    """Cumulative R after each closed trade (filled-only)."""
    cum = 0.0
    out: list[dict[str, Any]] = []
    for i, t in enumerate(trades, start=1):
        if t.outcome == "not_filled":
            continue
        r = t.pnl_r if t.pnl_r is not None else 0.0
        cum += r
        out.append(
            {
                "trade_index": i,
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "date": t.date,
                "exit_time": t.exit_time,
                "pnl_r": _round(r),
                "cumulative_r": _round(cum),
            }
        )
    return out


def backtest_intraday_smc(
    project_root: Path,
    cfg: BacktestConfig,
) -> BacktestRun:
    """Run the no-lookahead backtest for ``cfg``.

    Loads 1m bars from ``data/candles/{SYMBOL}/1min/*.csv``.
    Symbols with NO cached 1m data produce an entry in
    :attr:`BacktestRun.notes` and are skipped (no exception).
    """
    started = datetime.utcnow().isoformat() + "Z"
    if cfg.execution_allowed:  # double-defence
        raise ValueError("backtest_intraday_smc: execution_allowed must be False")

    all_trades: list[Trade] = []
    total_signals = 0
    notes: list[str] = []

    if not cfg.symbols:
        notes.append("no symbols supplied; nothing to backtest.")
    for symbol in cfg.symbols:
        bars = load_candles(
            project_root, symbol, "1min", start=cfg.start, end=cfg.end
        )
        if not bars:
            notes.append(
                f"{symbol}: no cached 1min candles for {cfg.start}..{cfg.end}; "
                "run 'fetch-candles' first."
            )
            continue
        sym_trades, sym_signals = _simulate_symbol(
            symbol, bars, cfg, notes_out=notes
        )
        all_trades.extend(sym_trades)
        total_signals += sym_signals

    metrics = compute_metrics(all_trades, total_signals=total_signals)
    equity = _equity_curve(all_trades)
    finished = datetime.utcnow().isoformat() + "Z"
    return BacktestRun(
        cfg=cfg,
        trades=all_trades,
        metrics=metrics,
        equity_curve=equity,
        notes=notes,
        started_at_utc=started,
        finished_at_utc=finished,
    )


__all__ = [
    "ALLOWED_DIRECTIONS",
    "ALLOWED_MODES",
    "BACKTEST_STRATEGY_KEY",
    "BacktestConfig",
    "BacktestRun",
    "DIRECTION_BOTH",
    "DIRECTION_LONG_ONLY",
    "DIRECTION_SHORT_ONLY",
    "ENTRY_CLOSE_TIME",
    "ENTRY_OPEN_TIME",
    "EOD_FORCE_FLAT_TIME",
    "MODE_AGGRESSIVE_ONLY",
    "MODE_BOTH",
    "MODE_STRICT_ONLY",
    "Trade",
    "backtest_intraday_smc",
    "resample_bars",
]
