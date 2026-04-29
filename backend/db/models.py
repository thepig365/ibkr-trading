"""Dataclasses representing SQLite records used by the engine.

These objects mirror the Paper-phase database schema and give the rest of the
backend typed, explicit data containers for writes and reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class TradeRecord:
    """A row in the trades table."""

    trade_id: str
    symbol: str
    strategy: str
    direction: str
    entry_price: float
    entry_time: datetime
    entry_shares: int
    stop_loss: float
    take_profit: float
    entry_reason: Optional[str] = None
    entry_signal_score: Optional[float] = None
    entry_fvg_top: Optional[float] = None
    entry_fvg_bottom: Optional[float] = None
    risk_amount: Optional[float] = None
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_shares: Optional[int] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    realized_r: Optional[float] = None
    holding_minutes: Optional[int] = None
    trailing_activated: bool = False
    trailing_stop_final: Optional[float] = None
    max_price_reached: Optional[float] = None
    status: str = "open"


@dataclass(frozen=True)
class ScaleInRecord:
    """A row in the scale_ins table."""

    trade_id: str
    price: float
    shares: int
    time: datetime
    reason: str
    time_unix: int
    id: Optional[int] = None


@dataclass(frozen=True)
class CandleSnapshot:
    """A row in the candle_snapshots table."""

    symbol: str
    timeframe: str
    timestamp: datetime
    time_unix: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_id: Optional[str] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class DailyPerformanceRecord:
    """A row in the daily_performance table."""

    date: date
    starting_equity: float
    ending_equity: float
    daily_pnl: float
    trades_count: int
    wins: int
    losses: int
    win_rate: float
    avg_r: float
    max_drawdown_pct: float
    capital_used: float
    circuit_broken: bool = False


@dataclass(frozen=True)
class SignalRecord:
    """A row in the signals table."""

    signal_id: str
    symbol: str
    strategy: str
    direction: str
    timestamp: datetime
    score: float
    auto_execute: bool
    executed: bool
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    reject_reason: Optional[str] = None


@dataclass(frozen=True)
class AccountSnapshot:
    """A row in the account_snapshots table."""

    timestamp: datetime
    net_liquidation: float
    cash: float
    unrealized_pnl: float
    realized_pnl_day: float
