"""Finnhub earnings, economic calendar, scored news, and blackout overlays."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Callable, Optional

import pytz

from backend.config import AppConfig

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")

WATCH_OR_POSITION_SCORE = 40

HIGH_IMPACT_KEYWORDS = (
    "earnings",
    "guidance",
    "merger",
    "acquisition",
    "fed",
    "fomc",
    "cpi",
    "jobs",
    "nfp",
    "sec",
    "lawsuit",
    "downgrade",
    "upgrade",
)
KEYWORD_BOOST = 30

RTH_ET_START = time(9, 30)
RTH_ET_END = time(16, 0)
RTH_BOOST = 20

STRONG_WORDS_DOWN = ("plunge", "crash", "tank", "slump", "sued", "probe")
STRONG_WORDS_UP = ("soar", "surge", "beats", "smash")

SENTIMENT_BOOST = 10

HIGH_IMPACT_MIN_SCORE = 60

KEYWORD_BLACKOUT_MINUTES = 5


@dataclass
class Blackout:
    """Symbol-level blackout window."""

    symbol: str
    starts_at: datetime
    ends_at: datetime
    reason: str

    def is_active(self, now: datetime) -> bool:
        return self.starts_at <= now <= self.ends_at


@dataclass
class NewsAlertPayload:
    symbol: str
    headline: str
    score: int
    source: str
    url: str
    reasons: list[str]


class FinnhubFeed:
    """Earnings/econ calendar + blackout + scored-news sweep (never places orders)."""

    def __init__(
        self,
        config: AppConfig,
        *,
        position_resolver: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        self.config = config
        self.api_key = config.finnhub.api_key or ""
        self.watchlist = list(config.finnhub.watchlist or config.symbols)
        self._blackouts: list[Blackout] = []
        self._earnings_today: list[dict[str, Any]] = []
        self._economic_today: list[dict[str, Any]] = []
        self._economic_error: Optional[str] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._client: Any = None
        self._position_symbols_resolver = position_resolver or (lambda: [])
        self._high_impact_handler: Optional[
            Callable[[list[NewsAlertPayload]], Any]
        ] = None
        self._emitted_news_fp: "OrderedDict[str, None]" = OrderedDict()
        self._max_fp_track = 2000
        self._last_high_impact_news: list[NewsAlertPayload] = []

    def is_finnhub_live(self) -> bool:
        """True when Finnhub SDK client initialized (API key valid path)."""

        return self._client is not None

    def set_high_impact_handler(
        self,
        handler: Optional[Callable[[list[NewsAlertPayload]], Any]],
    ) -> None:
        self._high_impact_handler = handler

    def bind_position_resolver(self, resolver: Callable[[], list[str]]) -> None:
        self._position_symbols_resolver = resolver

    async def start(self, symbols: list[str]) -> None:
        self.watchlist = list(set(self.watchlist) | set(symbols))
        if not self.api_key or self.api_key.startswith("your_"):
            logger.info("Finnhub API key not configured; news layer disabled")
            return
        try:
            import finnhub  # type: ignore

            self._client = finnhub.Client(api_key=self.api_key)
        except Exception:  # noqa: BLE001
            logger.exception("Finnhub client init failed; disabling")
            self._client = None
            return
        try:
            await self.refresh(
                position_symbols=list(self._position_symbols_resolver()),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Initial Finnhub refresh failed")
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
            await asyncio.sleep(30 * 60)
            try:
                await self.refresh(
                    position_symbols=list(self._position_symbols_resolver()),
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Finnhub refresh failed")

    async def economic_events_today(self) -> tuple[list[dict[str, Any]], Optional[str]]:
        if self._client is None:
            return [], "economic calendar unavailable (Finnhub disabled)"

        today = datetime.now(NY).date()
        try:
            data = await asyncio.to_thread(
                self._client.calendar_economic,
                _from=today.isoformat(),
                to=today.isoformat(),
            )
        except Exception:  # noqa: BLE001
            logger.exception("calendar_economic failed")
            return [], "economic calendar unavailable (API error)"

        out: list[dict[str, Any]] = []
        if isinstance(data, dict):
            raw = (
                data.get("economicCalendar")
                or data.get("economic_calendar")
                or []
            )
            if isinstance(raw, list):
                out.extend(raw)

        return out, None

    def _headline_fp(self, symbol: str, headline: str) -> str:
        h = (headline or "").strip().lower()[:320]
        return hashlib.sha256(f"{symbol.upper()}|{h}".encode()).hexdigest()

    def _already_emitted(self, fp: str) -> bool:
        return fp in self._emitted_news_fp

    def _register_emitted_fp(self, fp: str) -> None:
        self._emitted_news_fp[fp] = None
        while len(self._emitted_news_fp) > self._max_fp_track:
            self._emitted_news_fp.popitem(last=False)

    def _inside_rth_ny(self, dt: datetime) -> bool:
        if dt.astimezone(NY).weekday() >= 5:
            return False
        tloc = dt.astimezone(NY).time()
        return RTH_ET_START <= tloc <= RTH_ET_END

    def score_news_item(
        self,
        symbol: str,
        item: dict[str, Any],
        *,
        now_et: datetime,
        open_position_symbols: set[str],
        watch_symbols: set[str],
    ) -> tuple[int, list[str]]:
        reasons: list[str] = []
        score = 0
        sym_u = symbol.upper()
        in_watch = sym_u in watch_symbols
        in_pos = sym_u in open_position_symbols

        if in_watch or in_pos:
            score += WATCH_OR_POSITION_SCORE
            reasons.append("+watch_or_position")

        headline = (
            item.get("headline")
            or item.get("summary")
            or ""
        )
        lowered = (
            str(headline) + " " + str(item.get("summary") or "")
        ).lower()

        kw_hit = any(k in lowered for k in HIGH_IMPACT_KEYWORDS)
        if kw_hit:
            score += KEYWORD_BOOST
            reasons.append("+keyword")

        if self._inside_rth_ny(now_et):
            score += RTH_BOOST
            reasons.append("+rth")

        sentiment_boost_applied = False
        sentiment = item.get("sentiment")
        if isinstance(sentiment, dict):
            pct = sentiment.get("bullishPercent") or sentiment.get("bullish")
            try:
                p = float(pct)
                if abs(p - 50) >= 15:
                    score += SENTIMENT_BOOST
                    sentiment_boost_applied = True
                    reasons.append("+sentiment")
            except (TypeError, ValueError):
                pass
        if not sentiment_boost_applied:
            lowered_all = lowered
            if any(w in lowered_all for w in STRONG_WORDS_DOWN + STRONG_WORDS_UP):
                score += SENTIMENT_BOOST
                reasons.append("+headline_tone")

        return score, reasons

    def get_high_impact_news(
        self,
        min_score: int = HIGH_IMPACT_MIN_SCORE,
        limit: int = 10,
    ) -> list[NewsAlertPayload]:
        merged = sorted(
            self._last_high_impact_news,
            key=lambda p: (-p.score, p.symbol),
        )
        return [p for p in merged if p.score >= min_score][:limit]

    def _maybe_add_keyword_blackout(
        self,
        *,
        symbol: str,
        headline: str,
        reasons: list[str],
        now_et: datetime,
    ) -> None:
        if "+keyword" not in reasons:
            return
        hl = headline.lower()
        if not any(k in hl for k in HIGH_IMPACT_KEYWORDS):
            return
        end = now_et + timedelta(minutes=KEYWORD_BLACKOUT_MINUTES)
        day_end = now_et.replace(hour=23, minute=59, second=59)
        blk = Blackout(
            symbol=symbol.upper(),
            starts_at=now_et,
            ends_at=min(end, day_end),
            reason=f"keyword_blackout:{KEYWORD_BLACKOUT_MINUTES}m",
        )
        self._blackouts.append(blk)

    async def refresh(
        self,
        *,
        position_symbols: Optional[list[str]] = None,
    ) -> None:
        """Refresh earnings blackout, econ list, sweep news per watchlist ticker."""

        if self._client is None:
            return

        today = datetime.now(NY).date()
        now_et = datetime.now(NY)
        wl_set = {s.upper() for s in self.watchlist}

        open_syms = (
            set(s.upper() for s in position_symbols)
            if position_symbols is not None
            else {s.upper() for s in self._position_symbols_resolver()}
        )

        try:
            calendar = await asyncio.to_thread(
                self._client.earnings_calendar,
                _from=today.isoformat(),
                to=today.isoformat(),
                symbol="",
                international=False,
            )
            self._earnings_today = list(
                (calendar or {}).get("earningsCalendar") or [],
            )
        except Exception:  # noqa: BLE001
            logger.exception("earnings_calendar fetch failed")
            self._earnings_today = []

        econ, econ_err = await self.economic_events_today()
        self._economic_today = list(econ)
        self._economic_error = econ_err

        self._blackouts = []
        for entry in self._earnings_today:
            sym = (entry.get("symbol") or "").upper()
            if not sym or sym not in wl_set:
                continue
            start = NY.localize(datetime.combine(today, time(0, 0)))
            end = NY.localize(datetime.combine(today, time(23, 59)))
            self._blackouts.append(
                Blackout(sym, start, end, "earnings_full_day"),
            )

        alerts: list[NewsAlertPayload] = []
        to_notify: list[NewsAlertPayload] = []
        yesterday = today - timedelta(days=1)

        for sym in sorted(wl_set):
            try:
                raw = await asyncio.to_thread(
                    self._client.company_news,
                    sym,
                    _from=yesterday.isoformat(),
                    to=today.isoformat(),
                )
            except Exception:  # noqa: BLE001
                logger.exception("company_news sweep failed %s", sym)
                continue

            for item in list((raw or [])[:15]):
                if not isinstance(item, dict):
                    continue
                sc, rs = self.score_news_item(
                    sym,
                    item,
                    now_et=now_et,
                    open_position_symbols=open_syms,
                    watch_symbols=wl_set,
                )
                headline = (
                    item.get("headline")
                    or item.get("summary")
                    or "(no headline)"
                )
                headline = str(headline)
                fp = self._headline_fp(sym, headline)

                self._maybe_add_keyword_blackout(
                    symbol=sym,
                    headline=headline,
                    reasons=rs,
                    now_et=now_et,
                )

                if sc < HIGH_IMPACT_MIN_SCORE:
                    continue

                if self._already_emitted(fp):
                    continue

                payload = NewsAlertPayload(
                    symbol=sym.upper(),
                    headline=headline[:400],
                    score=sc,
                    source=str(
                        item.get("source")
                        or item.get("category")
                        or "finnhub"
                    )[:80],
                    url=str(item.get("url") or "")[:512],
                    reasons=rs,
                )
                alerts.append(payload)
                to_notify.append(payload)
                self._register_emitted_fp(fp)

        self._last_high_impact_news = sorted(
            alerts,
            key=lambda p: (-p.score, p.symbol),
        )

        if self._high_impact_handler and to_notify:
            res = self._high_impact_handler(to_notify[:50])
            if asyncio.iscoroutine(res):
                await res

    def blackout_map(self) -> dict[str, bool]:
        now = datetime.now(NY)
        return {
            b.symbol.upper(): True
            for b in self._blackouts
            if b.is_active(now)
        }

    def active_blackouts(self) -> list[dict[str, Any]]:
        now = datetime.now(NY)
        rows: list[dict[str, Any]] = []
        for b in self._blackouts:
            if not b.is_active(now):
                continue
            rows.append(
                {
                    "symbol": b.symbol,
                    "reason": b.reason,
                    "until": b.ends_at.astimezone(NY).isoformat(),
                }
            )
        return rows

    def earnings_today(self) -> list[dict[str, Any]]:
        return list(self._earnings_today)

    def economic_events_snapshot(self) -> dict[str, Any]:
        return {
            "events": list(self._economic_today),
            "error": self._economic_error,
        }

    async def fetch_company_news(
        self,
        symbol: str,
        limit: int = 5,
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
