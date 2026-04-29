"""Strategy-layer data objects shared across the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    """Trade direction."""

    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar at a given timeframe.

    Timestamps must be timezone-aware (America/New_York).
    """

    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


@dataclass
class Signal:
    """A strategy-emitted entry signal."""

    strategy_name: str
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    reason: str
    timeframe: str
    timestamp: datetime
    auto_execute: bool = False
    fvg_top: Optional[float] = None
    fvg_bottom: Optional[float] = None
    score: float = 0.0


@dataclass
class TradePosition:
    """An open trade position tracked by the trade manager."""

    trade_id: str
    symbol: str
    strategy: str
    direction: Direction
    entry_price: float
    entry_time: datetime
    shares: int
    stop_loss: float
    take_profit: float
    risk_per_share: float
    risk_amount: float
    fvg_top: Optional[float] = None
    fvg_bottom: Optional[float] = None
    entry_reason: Optional[str] = None
    entry_signal_score: Optional[float] = None
    trailing_activated: bool = False
    trailing_stop: Optional[float] = None
    moved_to_breakeven: bool = False
    max_price_reached: Optional[float] = None
    min_price_reached: Optional[float] = None
    scale_in_count: int = 0
    scale_in_records: list[dict] = field(default_factory=list)
    parent_order_id: Optional[int] = None
    stop_order_id: Optional[int] = None
    take_profit_order_id: Optional[int] = None
    closed: bool = False

    def current_r(self, price: float) -> float:
        """Compute the current trade in R-multiples for the given price."""

        if self.risk_per_share <= 0:
            return 0.0
        if self.direction is Direction.LONG:
            return (price - self.entry_price) / self.risk_per_share
        return (self.entry_price - price) / self.risk_per_share
