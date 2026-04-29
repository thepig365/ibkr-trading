"""IBKR market data subscription and 1-minute candle persistence.

Two subscription modes are supported:

* ``"keepUpToDate"`` (default) — uses ``reqHistoricalDataAsync`` with
  ``barSizeSetting="1 min"`` and ``keepUpToDate=True``. IBKR streams completed
  1-minute bars (and one in-progress bar) and we only emit on the
  ``has_new_bar`` boundary, which guarantees the previous bar is closed.

* ``"realtime"`` — uses ``reqRealTimeBars`` (5-second bars) and aggregates them
  into completed 1-minute candles via :class:`BarAggregator`. ICT does not run
  on 5-second bars; this path exists so the engine has a true 1-minute candle
  even when the historical feed is unavailable.

Either path stores the resulting 1-minute candles to SQLite and emits an
``on_bar`` callback for the trading engine. Outstanding callback tasks are
tracked so they cannot be silently garbage-collected.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal, Optional, Union

import pytz
from ib_insync import BarData, BarDataList, IB, RealTimeBar, Stock

from backend.data.bar_aggregator import BarAggregator, CompletedBar
from backend.db.database import Database
from backend.db.models import CandleSnapshot

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")
BarCallback = Callable[[str, dict[str, Any]], Union[Awaitable[None], None]]
SubscriptionMode = Literal["keepUpToDate", "realtime"]


class IBKRDataFeed:
    """Subscribe to IBKR 1-minute bars and persist candle snapshots."""

    def __init__(
        self,
        ib: IB,
        database: Database,
        *,
        on_bar: Optional[BarCallback] = None,
        mode: SubscriptionMode = "keepUpToDate",
    ) -> None:
        self.ib = ib
        self.database = database
        self.on_bar = on_bar
        self.mode = mode
        self._subscriptions: dict[str, BarDataList] = {}
        self._realtime_subs: dict[str, Any] = {}
        self._last_seen_timestamp: dict[str, datetime] = {}
        self._aggregator = BarAggregator()
        self._tasks: set[asyncio.Task[None]] = set()

    def set_on_bar(self, callback: Optional[BarCallback]) -> None:
        """Wire (or rewire) the bar callback after construction."""

        self.on_bar = callback

    async def subscribe_symbols(self, symbols: list[str]) -> None:
        """Subscribe to 1-minute bars for a list of US stock symbols."""

        for symbol in symbols:
            try:
                await self.subscribe_1m_bars(symbol)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to subscribe %s", symbol)

    async def subscribe_1m_bars(self, symbol: str) -> None:
        """Subscribe to one symbol's 1-minute completed-bar stream."""

        normalized = symbol.upper().strip()
        if normalized in self._subscriptions or normalized in self._realtime_subs:
            logger.info("Already subscribed to %s", normalized)
            return

        if not self.ib.isConnected():
            raise ConnectionError("Cannot subscribe to bars before IBKR is connected")

        contract = Stock(normalized, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)

        if self.mode == "keepUpToDate":
            bars = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="2 D",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
                keepUpToDate=True,
            )
            bars.updateEvent += self._make_history_handler(normalized)
            self._subscriptions[normalized] = bars
            logger.info("Subscribed to %s 1m keepUpToDate stream", normalized)
        else:
            sub = self.ib.reqRealTimeBars(
                contract, 5, "TRADES", useRTH=True
            )
            sub.updateEvent += self._make_realtime_handler(normalized)
            self._realtime_subs[normalized] = sub
            logger.info(
                "Subscribed to %s 5s realtime bars (aggregating to 1m)", normalized
            )

    async def unsubscribe_all(self) -> None:
        """Cancel all active market data subscriptions and stop pending tasks."""

        for symbol, bars in list(self._subscriptions.items()):
            try:
                self.ib.cancelHistoricalData(bars)
                logger.info("Unsubscribed from %s 1m bars", symbol)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to unsubscribe from %s", symbol)
        self._subscriptions.clear()

        for symbol, sub in list(self._realtime_subs.items()):
            try:
                self.ib.cancelRealTimeBars(sub)
                logger.info("Unsubscribed from %s realtime bars", symbol)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to unsubscribe realtime %s", symbol)
        self._realtime_subs.clear()
        self._aggregator.reset()
        self._last_seen_timestamp.clear()

        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    def _track(self, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _make_history_handler(
        self, symbol: str
    ) -> Callable[[BarDataList, bool], None]:
        def _handler(bars: BarDataList, has_new_bar: bool) -> None:
            if not has_new_bar or len(bars) < 2:
                return
            completed_bar = bars[-2]
            self._track(self._process_completed_bar(symbol, completed_bar))

        return _handler

    def _make_realtime_handler(
        self, symbol: str
    ) -> Callable[[Any, RealTimeBar], None]:
        def _handler(bars: Any, latest: RealTimeBar) -> None:
            bar_time = latest.time
            if isinstance(bar_time, datetime) and bar_time.tzinfo is None:
                bar_time = pytz.UTC.localize(bar_time)
            elif not isinstance(bar_time, datetime):
                return

            completed = self._aggregator.ingest(
                symbol,
                bar_time,
                float(latest.open_),
                float(latest.high),
                float(latest.low),
                float(latest.close),
                int(getattr(latest, "volume", 0) or 0),
            )
            if completed is not None:
                self._track(self._emit_completed_minute(completed))

        return _handler

    async def _process_completed_bar(self, symbol: str, bar: BarData) -> None:
        timestamp = self._normalize_bar_datetime(bar.date)
        last_seen = self._last_seen_timestamp.get(symbol)
        if last_seen and timestamp <= last_seen:
            return
        self._last_seen_timestamp[symbol] = timestamp

        payload = {
            "symbol": symbol,
            "timeframe": "1m",
            "timestamp": timestamp,
            "time_unix": int(timestamp.timestamp()),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(getattr(bar, "volume", 0) or 0),
        }
        await self._persist_and_dispatch(symbol, payload)

    async def _emit_completed_minute(self, completed: CompletedBar) -> None:
        last_seen = self._last_seen_timestamp.get(completed.symbol)
        if last_seen and completed.timestamp <= last_seen:
            return
        self._last_seen_timestamp[completed.symbol] = completed.timestamp

        payload = {
            "symbol": completed.symbol,
            "timeframe": "1m",
            "timestamp": completed.timestamp,
            "time_unix": completed.time_unix,
            "open": completed.open,
            "high": completed.high,
            "low": completed.low,
            "close": completed.close,
            "volume": completed.volume,
        }
        await self._persist_and_dispatch(completed.symbol, payload)

    async def _persist_and_dispatch(
        self, symbol: str, payload: dict[str, Any]
    ) -> None:
        candle = CandleSnapshot(
            symbol=payload["symbol"],
            timeframe=payload["timeframe"],
            timestamp=payload["timestamp"],
            time_unix=payload["time_unix"],
            open=payload["open"],
            high=payload["high"],
            low=payload["low"],
            close=payload["close"],
            volume=payload["volume"],
        )
        try:
            await self.database.insert_candle_snapshot(candle)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist 1m candle for %s", symbol)

        if self.on_bar:
            try:
                result = self.on_bar(symbol, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("on_bar callback failed for %s", symbol)

    @staticmethod
    def _normalize_bar_datetime(value: Any) -> datetime:
        """Normalize an IBKR bar timestamp to timezone-aware New York time."""

        if isinstance(value, datetime):
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                return NY.localize(value)
            return value.astimezone(NY)

        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
                return NY.localize(parsed)
            return parsed.astimezone(NY)

        raise TypeError(f"Unsupported bar timestamp type: {type(value)!r}")
