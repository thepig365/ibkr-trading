"""Finnhub-backed earnings calendar + news blackout source.

The implementation is async-friendly and degrades gracefully when no API key is
configured. It pulls today's earnings calendar at startup, and refreshes every
30 minutes. News blackouts are populated from earnings dates and any keyword
hits in the company news endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Optional

import pytz

from backend.config import AppConfig

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


@dataclass
class Blackout:
    """A symbol-level news blackout entry."""

    symbol: str
    starts_at: datetime
    ends_at: datetime
    reason: str

    def is_active(self, now: datetime) -> bool:
        return self.starts_at <= now <= self.ends_at


class FinnhubFeed:
    """Earnings calendar + news blackout manager."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.api_key = config.finnhub.api_key
        self.watchlist = config.finnhub.watchlist or config.symbols
        self._blackouts: list[Blackout] = []
        self._earnings_today: list[dict[str, Any]] = []
        self._task: Optional[asyncio.Task[None]] = None
        self._client: Any = None

    async def start(self, symbols: list[str]) -> None:
        if not self.api_key or self.api_key.startswith("your_"):
            logger.info("Finnhub API key not configured; news layer disabled")
            return
        self.watchlist = list(set(self.watchlist) | set(symbols))
        try:
            import finnhub  # type: ignore

            self._client = finnhub.Client(api_key=self.api_key)
        except Exception:  # noqa: BLE001
            logger.exception("Finnhub client init failed; disabling")
            self._client = None
            return
        self._task = asyncio.create_task(self._run_loop(), name="finnhub-feed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                logger.exception("Finnhub refresh failed")
            await asyncio.sleep(30 * 60)

    async def refresh(self) -> None:
        if self._client is None:
            return
        today = datetime.now(NY).date()
        try:
            calendar = await asyncio.to_thread(
                self._client.earnings_calendar,
                _from=today.isoformat(),
                to=today.isoformat(),
                symbol="",
                international=False,
            )
            self._earnings_today = list(
                (calendar or {}).get("earningsCalendar", [])
            )
        except Exception:  # noqa: BLE001
            logger.exception("earnings_calendar fetch failed")
            self._earnings_today = []

        self._blackouts = []
        for entry in self._earnings_today:
            symbol = entry.get("symbol")
            if not symbol or symbol not in self.watchlist:
                continue
            start = NY.localize(datetime.combine(today, time(0, 0)))
            end = NY.localize(datetime.combine(today, time(23, 59)))
            self._blackouts.append(
                Blackout(symbol=symbol, starts_at=start, ends_at=end, reason="earnings")
            )

    def blackout_map(self) -> dict[str, bool]:
        now = datetime.now(NY)
        return {
            blackout.symbol: True
            for blackout in self._blackouts
            if blackout.is_active(now)
        }

    def earnings_today(self) -> list[dict[str, Any]]:
        return list(self._earnings_today)

    async def fetch_company_news(
        self, symbol: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        today = datetime.now(NY).date()
        a_week_ago = today - timedelta(days=7)
        try:
            data = await asyncio.to_thread(
                self._client.company_news,
                symbol,
                _from=a_week_ago.isoformat(),
                to=today.isoformat(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("company_news failed for %s", symbol)
            return []
        return list((data or [])[:limit])
