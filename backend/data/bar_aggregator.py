"""5-second to 1-minute bar aggregator.

`ib_insync.IB.reqRealTimeBars` returns 5-second bars. ICT strategy needs
completed 1-minute candles, so we aggregate 12 consecutive 5-second bars into
one 1-minute bar bucketed on the New York wall clock minute. A bar is emitted
only when the next-minute bucket opens, which guarantees the 1-minute bar is
fully closed before the strategy sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pytz

NY = pytz.timezone("America/New_York")


@dataclass
class _MinuteBucket:
    """Open running 1-minute aggregation state."""

    minute_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    bar_count: int = 0


@dataclass
class CompletedBar:
    """An emitted, finalised 1-minute bar."""

    symbol: str
    timestamp: datetime
    time_unix: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarAggregator:
    """Aggregate 5-second bars into completed 1-minute bars per symbol."""

    def __init__(self) -> None:
        self._buckets: dict[str, _MinuteBucket] = {}

    def reset(self, symbol: Optional[str] = None) -> None:
        """Clear running state for one symbol or all symbols."""

        if symbol is None:
            self._buckets.clear()
            return
        self._buckets.pop(symbol, None)

    def ingest(
        self,
        symbol: str,
        bar_time: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ) -> Optional[CompletedBar]:
        """Add one 5-second bar; return a completed 1-minute bar if a minute closed.

        `bar_time` must be timezone-aware. The bucket boundary is the 5-second
        bar's start minute in America/New_York. When a new 5-second bar arrives
        whose minute is later than the current bucket's minute, the running
        bucket is emitted and a fresh one is opened.
        """

        if bar_time.tzinfo is None:
            raise ValueError("bar_time must be timezone-aware")

        ny_time = bar_time.astimezone(NY)
        minute_start = ny_time.replace(second=0, microsecond=0)

        emitted: Optional[CompletedBar] = None
        bucket = self._buckets.get(symbol)
        if bucket is None:
            self._buckets[symbol] = _MinuteBucket(
                minute_start=minute_start,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=int(volume),
                bar_count=1,
            )
            return None

        if minute_start > bucket.minute_start:
            emitted = self._emit(symbol, bucket)
            self._buckets[symbol] = _MinuteBucket(
                minute_start=minute_start,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=int(volume),
                bar_count=1,
            )
            return emitted

        if minute_start < bucket.minute_start:
            return None

        bucket.high = max(bucket.high, high)
        bucket.low = min(bucket.low, low)
        bucket.close = close
        bucket.volume += int(volume)
        bucket.bar_count += 1
        return None

    def flush(self, symbol: str) -> Optional[CompletedBar]:
        """Force-emit the in-progress bucket for a symbol (e.g. on shutdown)."""

        bucket = self._buckets.pop(symbol, None)
        if bucket is None:
            return None
        return self._emit(symbol, bucket)

    @staticmethod
    def _emit(symbol: str, bucket: _MinuteBucket) -> CompletedBar:
        return CompletedBar(
            symbol=symbol,
            timestamp=bucket.minute_start,
            time_unix=int(bucket.minute_start.timestamp()),
            open=bucket.open,
            high=bucket.high,
            low=bucket.low,
            close=bucket.close,
            volume=bucket.volume,
        )
