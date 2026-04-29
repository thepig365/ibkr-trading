"""TradingEngine - dispatches bars to the strategy, applies risk + trade mgmt."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, time, timedelta
from typing import Any, Optional

import pytz

from backend.config import AppConfig
from backend.connection.connection_manager import ConnectionManager, ConnectionState
from backend.db.database import Database
from backend.db.models import SignalRecord
from backend.execution.risk_manager import RiskManager
from backend.execution.trade_manager import TradeManager
from backend.notifications.finnhub_feed import FinnhubFeed, NewsAlertPayload

from backend.notifications.telegram_bot import TelegramBot
from backend.strategy.base import BaseStrategy
from backend.strategy.ict_strategy import ICTStrategy
from backend.strategy.models import Bar, Direction, Signal, TradePosition

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")
EOD_FORCE_CLOSE = time(15, 45)
PREMARKET_BIAS = time(9, 0)


class TradingEngine:
    """Drive the strategy with each new 1-minute bar and orchestrate execution."""

    def __init__(
        self,
        *,
        config: AppConfig,
        database: Database,
        connection_manager: ConnectionManager,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        trade_manager: TradeManager,
        news_feed: FinnhubFeed,
        telegram_bot: TelegramBot,
    ) -> None:
        self.config = config
        self.database = database
        self.connection_manager = connection_manager
        self.strategy = strategy
        self.risk = risk_manager
        self.trades = trade_manager
        self.news_feed = news_feed
        self.telegram_bot = telegram_bot
        self.paused: bool = False
        self._eod_done_for: Optional[Any] = None
        self._bias_done_for: Optional[Any] = None
        self._daily_news_bundle_sent_date: Optional[Any] = None
        self.trades.set_on_close(self._on_trade_close)

    async def start(self) -> None:
        logger.info("TradingEngine started; strategy=%s", self.strategy.name)
        asyncio.create_task(self._send_startup_news_summary())

    def news_open_symbols(self) -> list[str]:
        return [
            sym
            for sym, pos in self.trades.positions.items()
            if not pos.closed
        ]

    def _watchlist_earnings_today_rows(self) -> list[dict[str, Any]]:
        wl = {s.upper() for s in self.news_feed.watchlist}
        return [
            e
            for e in self.news_feed.earnings_today()
            if str(e.get("symbol", "")).upper() in wl
        ]

    async def deliver_high_impact_news_alerts(
        self, payloads: list[NewsAlertPayload]
    ) -> None:
        for p in payloads:
            await self.telegram_bot.send_news_alert(
                symbol=p.symbol,
                headline=p.headline,
                score=p.score,
                source=p.source,
                url=p.url,
                reason=";".join(p.reasons),
            )

    async def _send_daily_news_bundle(self, tag: str) -> None:
        nf = self.news_feed
        finnhub_disabled = not nf.is_finnhub_live()

        await self.telegram_bot.send_daily_news_digest(
            tag=tag,
            earnings=self._watchlist_earnings_today_rows(),
            economic=nf.economic_events_snapshot(),
            top_news=nf.get_high_impact_news(60, 6),
            blackouts=nf.active_blackouts() if nf.is_finnhub_live() else [],
            finnhub_disabled=finnhub_disabled,
        )
        if not finnhub_disabled:
            await self.telegram_bot.send_earnings_alert(
                self._watchlist_earnings_today_rows()
            )

    async def _send_startup_news_summary(self) -> None:
        await asyncio.sleep(0)
        await self._maybe_send_daily_news_bundle("startup")

    async def _maybe_send_daily_news_bundle(self, tag: str) -> None:
        today = datetime.now(NY).date()
        if self._daily_news_bundle_sent_date == today:
            return
        try:
            await self._send_daily_news_bundle(tag)
        except Exception:  # noqa: BLE001
            logger.exception("Daily news bundle failed (%s)", tag)
            return
        self._daily_news_bundle_sent_date = today

    async def stop(self) -> None:
        logger.info("TradingEngine stopped")

    # ---------- Public dispatch ---------- #

    async def on_bar(self, symbol: str, bar_payload: dict[str, Any]) -> None:
        bar = self._build_bar(symbol, bar_payload)
        if bar is None:
            return

        await self._update_open_position(symbol, bar)

        if self.paused:
            return
        if self.connection_manager.state != ConnectionState.CONNECTED:
            return
        if self.risk.check_circuit_breaker():
            return

        context = self._build_context(symbol, bar)
        signal = self.strategy.on_bar(bar, context)
        if signal is None:
            return

        await self._handle_signal(signal)

    async def _handle_signal(self, signal: Signal) -> None:
        ok, reject = self.risk.validate_signal(signal)
        record = SignalRecord(
            signal_id=str(uuid.uuid4()),
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            direction=signal.direction.value,
            timestamp=signal.timestamp,
            score=float(signal.score),
            auto_execute=signal.auto_execute,
            executed=False,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            reason=signal.reason,
            reject_reason=reject,
        )

        if not ok:
            await self.database.insert_signal(record)
            await self.telegram_bot.send_signal_alert(signal, executed=False, reject=reject)
            return

        if not signal.auto_execute:
            await self.database.insert_signal(record)
            await self.telegram_bot.send_signal_alert(signal, executed=False)
            return

        sizing = self.risk.size_signal(signal)
        if sizing is None:
            record = SignalRecord(
                signal_id=record.signal_id,
                symbol=record.symbol,
                strategy=record.strategy,
                direction=record.direction,
                timestamp=record.timestamp,
                score=record.score,
                auto_execute=record.auto_execute,
                executed=False,
                entry_price=record.entry_price,
                stop_loss=record.stop_loss,
                take_profit=record.take_profit,
                reason=record.reason,
                reject_reason="capital_cap_exceeded_or_zero_risk",
            )
            await self.database.insert_signal(record)
            await self.telegram_bot.send_signal_alert(
                signal, executed=False, reject="capital_cap_exceeded"
            )
            return

        position = await self.trades.open_trade(signal, sizing)
        if position is None:
            await self.database.insert_signal(record)
            return

        self.risk.consume_capital(sizing.notional)
        self.risk.register_trade_open()
        if isinstance(self.strategy, ICTStrategy):
            self.strategy.record_trade_for_day(signal.symbol)

        executed_record = SignalRecord(
            signal_id=record.signal_id,
            symbol=record.symbol,
            strategy=record.strategy,
            direction=record.direction,
            timestamp=record.timestamp,
            score=record.score,
            auto_execute=record.auto_execute,
            executed=True,
            entry_price=record.entry_price,
            stop_loss=record.stop_loss,
            take_profit=record.take_profit,
            reason=record.reason,
            reject_reason=None,
        )
        await self.database.insert_signal(executed_record)
        await self.telegram_bot.send_signal_alert(signal, executed=True)

    async def _update_open_position(self, symbol: str, bar: Bar) -> None:
        position = self.trades.positions.get(symbol)
        if position is None or position.closed:
            return

        # Trigger stop / take-profit hits in paper-only mode
        hit = self._detect_exit_hit(position, bar)
        if hit is not None:
            await self.trades.finalize_close(
                position,
                exit_price=hit[1],
                reason=hit[0],
                exit_time=bar.timestamp,
            )
            return

        await self.trades.on_price_update(symbol, bar.close, bar.timestamp)
        if position.scale_in_count < self.config.ict.max_scale_ins:
            added = await self.trades.maybe_scale_in(
                position, bar.close, bar.timestamp
            )
            if added is not None:
                self.risk.consume_capital(bar.close * added)

        update = self.strategy.on_position_update(
            {
                "symbol": position.symbol,
                "direction": position.direction.value,
                "entry_price": position.entry_price,
                "risk_per_share": position.risk_per_share,
                "stop_loss": position.stop_loss,
                "moved_to_breakeven": position.moved_to_breakeven,
                "last_price": bar.close,
            }
        )
        if update and update.get("action") == "move_stop":
            position.stop_loss = float(update["new_stop"])
            position.moved_to_breakeven = bool(update.get("moved_to_breakeven", True))
            await self.trades._modify_stop_order(position)  # type: ignore[attr-defined]

    def _detect_exit_hit(
        self, position: TradePosition, bar: Bar
    ) -> Optional[tuple[str, float]]:
        if position.direction is Direction.LONG:
            if bar.low <= position.stop_loss:
                return ("trailing_stop" if position.trailing_activated else "stop_loss", position.stop_loss)
            if bar.high >= position.take_profit:
                return ("take_profit", position.take_profit)
        else:
            if bar.high >= position.stop_loss:
                return ("trailing_stop" if position.trailing_activated else "stop_loss", position.stop_loss)
            if bar.low <= position.take_profit:
                return ("take_profit", position.take_profit)
        return None

    # ---------- EOD / pre-market ---------- #

    async def run_eod_loop(self) -> None:
        """Background loop: 9:00 AM bias compute; 3:45 PM force close."""

        while True:
            try:
                await asyncio.sleep(15)
                now = datetime.now(NY)
                today = now.date()

                if (
                    now.time() >= PREMARKET_BIAS
                    and now.time() < time(9, 30)
                    and self._bias_done_for != today
                ):
                    await self._compute_premarket_bias()
                    self._bias_done_for = today

                if (
                    now.time() >= PREMARKET_BIAS
                    and now.time() < time(9, 30)
                    and self._daily_news_bundle_sent_date != today
                ):
                    try:
                        if self.news_feed.is_finnhub_live():
                            await self.news_feed.refresh(
                                position_symbols=list(self.news_open_symbols()),
                            )
                        await self._maybe_send_daily_news_bundle("9am_et")
                    except Exception:  # noqa: BLE001
                        logger.exception("9:00 AM ET news bundle failed")

                if (
                    now.time() >= EOD_FORCE_CLOSE
                    and self._eod_done_for != today
                ):
                    await self.trades.force_close_all(reason="force_close_eod")
                    self._eod_done_for = today
                    await self.telegram_bot.send_text("End-of-day force close completed.")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("EOD loop iteration failed")

    async def _compute_premarket_bias(self) -> None:
        if not isinstance(self.strategy, ICTStrategy):
            return
        if not self.connection_manager.ib.isConnected():
            logger.info("Pre-market bias skipped: IBKR not connected")
            return
        for symbol in self.config.symbols:
            try:
                bars = await self._fetch_daily_bars(symbol)
                if not bars:
                    continue
                last = bars[-1]
                bias = self.strategy.compute_daily_bias(symbol, bars, last.close)
                self.strategy.set_daily_bias(symbol, bias)
                logger.info("Daily bias %s -> %s", symbol, bias)
            except Exception:  # noqa: BLE001
                logger.exception("Daily bias compute failed for %s", symbol)
        self.risk.begin_day()
        await self.telegram_bot.send_daily_bias_report(
            getattr(self.strategy, "_states", {})  # type: ignore[arg-type]
        )

    async def _fetch_daily_bars(self, symbol: str) -> list[Bar]:
        from ib_insync import Stock

        contract = Stock(symbol, "SMART", "USD")
        ib = self.connection_manager.ib
        try:
            await ib.qualifyContractsAsync(contract)
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="30 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch daily bars for %s", symbol)
            return []
        out: list[Bar] = []
        for raw in bars:
            ts = raw.date
            if isinstance(ts, datetime):
                ts_aware = (
                    ts if ts.tzinfo else NY.localize(ts)
                )
            else:
                ts_aware = NY.localize(datetime.combine(ts, time(16, 0)))
            out.append(
                Bar(
                    symbol=symbol,
                    timeframe="1d",
                    timestamp=ts_aware,
                    open=float(raw.open),
                    high=float(raw.high),
                    low=float(raw.low),
                    close=float(raw.close),
                    volume=int(getattr(raw, "volume", 0) or 0),
                )
            )
        return out

    # ---------- Helpers ---------- #

    def _build_bar(
        self, symbol: str, payload: dict[str, Any]
    ) -> Optional[Bar]:
        timestamp = payload.get("timestamp")
        if not isinstance(timestamp, datetime):
            return None
        if timestamp.tzinfo is None:
            timestamp = NY.localize(timestamp)
        else:
            timestamp = timestamp.astimezone(NY)
        return Bar(
            symbol=symbol,
            timeframe=payload.get("timeframe", "1m"),
            timestamp=timestamp,
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=int(payload.get("volume", 0) or 0),
        )

    def _build_context(self, symbol: str, bar: Bar) -> dict[str, Any]:
        return {
            "now_ny": bar.timestamp.astimezone(NY),
            "news_blackout": self.news_feed.blackout_map(),
            "daily_bias": {},  # bias is preloaded via set_daily_bias
            "open_position": self.trades.positions.get(symbol),
        }

    async def _on_trade_close(
        self, position: TradePosition, info: dict[str, Any]
    ) -> None:
        pnl = float(info.get("pnl", 0.0))
        r = float(info.get("r", 0.0))
        reason = str(info.get("reason", "manual"))
        self.risk.register_trade_close(pnl)
        exit_px = float(info.get("exit_price", position.entry_price))
        await self.telegram_bot.send_close_alert(
            symbol=position.symbol,
            entry=position.entry_price,
            exit_price=exit_px,
            pnl=pnl,
            r=r,
            reason=reason,
        )

    # ---------- Telegram-facing actions ---------- #

    async def pause(self) -> None:
        self.paused = True
        await self.telegram_bot.send_text("Engine paused.")

    async def resume(self) -> None:
        self.paused = False
        await self.telegram_bot.send_text("Engine resumed.")

    async def reconnect(self) -> None:
        await self.connection_manager.reconnect()

    def status_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "connection": self.connection_manager.state.value,
            "strategy": self.strategy.name,
            "risk": self.risk.status_dict(),
            "open_positions": self.trades.open_positions_dict(),
        }
