"""Async Telegram bot for outbound notifications and inbound commands.

Built against ``python-telegram-bot`` v20+. The bot runs in the background as a
long-poller and exposes ten chat commands. Outbound helpers format each
notification template defined in the engine spec.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import pytz

from backend.config import AppConfig
from backend.notifications.finnhub_feed import NewsAlertPayload
from backend.strategy.models import Signal

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from backend.engine.trading_engine import TradingEngine

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


class TelegramBot:
    """Send notifications and accept commands via Telegram."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id
        self._engine: Optional["TradingEngine"] = None
        self._app: Any = None
        self._enabled = bool(
            self.token
            and not self.token.startswith("your_")
            and self.chat_id
            and not self.chat_id.startswith("your_")
        )

    def bind_engine(self, engine: "TradingEngine") -> None:
        self._engine = engine

    async def start(self) -> None:
        if not self._enabled:
            logger.info("Telegram disabled (missing token or chat id)")
            return
        try:
            from telegram.ext import (
                Application,
                CommandHandler,
            )
        except Exception:  # noqa: BLE001
            logger.exception("python-telegram-bot import failed; disabling Telegram")
            self._enabled = False
            return

        application = Application.builder().token(self.token).build()
        application.add_handler(CommandHandler("status", self._cmd_status))
        application.add_handler(CommandHandler("positions", self._cmd_positions))
        application.add_handler(CommandHandler("pnl", self._cmd_pnl))
        application.add_handler(CommandHandler("pause", self._cmd_pause))
        application.add_handler(CommandHandler("resume", self._cmd_resume))
        application.add_handler(CommandHandler("close", self._cmd_close))
        application.add_handler(CommandHandler("news", self._cmd_news))
        application.add_handler(CommandHandler("bias", self._cmd_bias))
        application.add_handler(CommandHandler("reconnect", self._cmd_reconnect))
        application.add_handler(CommandHandler("report", self._cmd_report))

        try:
            await application.initialize()
            await application.start()
            if application.updater is not None:
                await application.updater.start_polling()
        except Exception:  # noqa: BLE001
            logger.exception("Telegram start failed; disabling")
            self._enabled = False
            return

        self._app = application
        logger.info("Telegram bot started")

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("Telegram shutdown failed")

    # ---------- Outbound helpers ---------- #

    async def send_text(self, text: str) -> None:
        if not self._enabled or self._app is None:
            return
        try:
            await self._app.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception:  # noqa: BLE001
            logger.exception("send_text failed")

    async def send_error(self, text: str) -> None:
        await self.send_text(f"[ERROR] {text}")

    async def send_signal_alert(
        self,
        signal: Signal,
        *,
        executed: bool,
        reject: Optional[str] = None,
    ) -> None:
        header = "AUTO-EXEC" if executed else ("ALERT" if reject is None else "REJECTED")
        msg = (
            f"[{header}] {signal.symbol} {signal.direction.value} | "
            f"score={signal.score:.0f} | entry={signal.entry_price:.2f} | "
            f"SL={signal.stop_loss:.2f} | TP={signal.take_profit:.2f} | "
            f"reason={signal.reason}"
        )
        if reject:
            msg += f" | reject={reject}"
        await self.send_text(msg)

    async def send_close_alert(
        self,
        *,
        symbol: str,
        entry: float,
        exit_price: float,
        pnl: float,
        r: float,
        reason: str,
    ) -> None:
        await self.send_text(
            f"[CLOSE] {symbol} entry={entry:.2f} exit={exit_price:.2f} "
            f"pnl={pnl:+.2f} R={r:+.2f} reason={reason}"
        )

    async def send_daily_bias_report(self, states: dict[str, Any]) -> None:
        if not states:
            return
        lines = [f"Daily Bias Report - {datetime.now(NY).strftime('%Y-%m-%d')}"]
        for symbol, state in states.items():
            bias = getattr(state, "bias", None)
            if bias is None:
                lines.append(f"{symbol}: no bias")
                continue
            lines.append(
                f"{symbol}: {bias.direction.value} eq={bias.equilibrium:.2f} "
                f"target={bias.target_price:.2f}"
            )
        await self.send_text("\n".join(lines))

    async def send_news_alert(
        self,
        *,
        symbol: str,
        headline: str,
        score: int,
        source: str,
        url: str,
        reason: str,
    ) -> None:
        url_line = f"\n{url}" if url else ""
        await self.send_text(
            "[NEWS HIGH-IMPACT]"
            f"\n{symbol} score={score}"
            f"\n{headline[:380]}"
            f"\n src={source} reasons={reason}"
            f"{url_line}"
            f"\n(not a trade signal; blackouts advisory only)"
        )


    async def send_earnings_alert(
        self,
        entries: list[dict[str, Any]],
    ) -> None:
        if not entries:
            await self.send_text("[EARNINGS] none on watchlist today")
            return
        lines = ["[EARNINGS today] watchlist:"]
        for e in entries[:20]:
            sym = e.get("symbol") or "?"
            eps = e.get("epsEstimate")
            act = e.get("epsActual")
            lines.append(f"- {sym}: est={eps} act={act}")
        await self.send_text("\n".join(lines))

    async def send_daily_news_digest(
        self,
        *,
        tag: str,
        earnings: list[dict[str, Any]],
        economic: dict[str, Any],
        top_news: list[NewsAlertPayload],
        blackouts: list[dict[str, Any]],
        finnhub_disabled: bool,
    ) -> None:
        hdr = f"[News digest — {tag} — {datetime.now(NY).strftime('%Y-%m-%d %H:%M')} ET]"
        lines = [hdr]
        if finnhub_disabled:
            lines.append("Finnhub unavailable (missing key or SDK error); no econ/news scrape.")
            await self.send_text("\n".join(lines))
            return
        ev = economic.get("events") or []
        econ_err = economic.get("error")
        if econ_err:
            lines.append(f"Economic calendar note: {econ_err}")
        if ev:
            lines.append("Economy (today, sample ≤6):")
            for row in ev[:6]:
                lines.append(str(row))
        elif not econ_err:
            lines.append("Economic calendar: no rows for today.")

        wl_earn = [e for e in earnings if str(e.get("symbol", "")).upper()]
        lines.append(f"Earnings touches watchlist: {len(wl_earn)}")
        lines.append("")
        lines.append(f"Blackouts active ({len(blackouts)}):")
        if not blackouts:
            lines.append("(none)")
        else:
            for b in blackouts[:20]:
                lines.append(f"- {b.get('symbol')}: {b.get('reason')} until {b.get('until')}")

        lines.append("")
        lines.append(f"High-impact headline batch (digest only, scored ≥60, top ≤5): {len(top_news)}")
        for p in top_news[:5]:
            lines.append(f"- {p.symbol} ({p.score}): {p.headline[:120]}")

        body = "\n".join(lines)
        if len(body) > 3500:
            body = body[:3480] + "\n...[truncated]"
        await self.send_text(body)

    # ---------- Inbound commands ---------- #

    async def _cmd_status(self, update: Any, context: Any) -> None:
        if not self._engine:
            await update.message.reply_text("Engine not bound")
            return
        status = self._engine.status_dict()
        await update.message.reply_text(
            f"State: {status['connection']} | strategy={status['strategy']} | "
            f"paused={status['paused']}"
        )

    async def _cmd_positions(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        positions = self._engine.trades.open_positions_dict()
        if not positions:
            await update.message.reply_text("No open positions")
            return
        lines = [
            f"{p['symbol']} {p['direction']} {p['shares']}@{p['entry_price']:.2f} "
            f"SL={p['stop_loss']:.2f} TP={p['take_profit']:.2f}"
            for p in positions
        ]
        await update.message.reply_text("\n".join(lines))

    async def _cmd_pnl(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        risk = self._engine.risk.status_dict()
        await update.message.reply_text(
            f"Today P&L={risk['realized_pnl_day']:.2f} "
            f"trades={risk['trades_today']}/{risk['max_trades_per_day']} "
            f"capital_used={risk['daily_capital_used']:.0f}/{risk['daily_capital_limit']:.0f}"
        )

    async def _cmd_pause(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        await self._engine.pause()
        await update.message.reply_text("Engine paused")

    async def _cmd_resume(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        await self._engine.resume()
        await update.message.reply_text("Engine resumed")

    async def _cmd_close(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        if not context.args:
            await update.message.reply_text("Usage: /close SYMBOL")
            return
        symbol = context.args[0].upper()
        ok = await self._engine.trades.manual_close(symbol)
        await update.message.reply_text(
            f"Closed {symbol}" if ok else f"No open position in {symbol}"
        )

    async def _cmd_news(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        if not context.args:
            await update.message.reply_text("Usage: /news SYMBOL")
            return
        symbol = context.args[0].upper()
        nf = self._engine.news_feed
        items = await nf.fetch_company_news(symbol, limit=8)
        if not items:
            await update.message.reply_text(f"No recent news for {symbol}")
            return
        wl_set = {s.upper() for s in nf.watchlist}
        open_syms = {s.upper() for s in self._engine.news_open_symbols()}
        now_et = datetime.now(NY)
        lines = [f"{symbol} (scored)"]
        for item in items:
            if not isinstance(item, dict):
                continue
            sc, rs = nf.score_news_item(
                symbol,
                item,
                now_et=now_et,
                open_position_symbols=open_syms,
                watch_symbols=wl_set,
            )
            headline = item.get("headline") or item.get("summary") or "(no title)"
            url = item.get("url") or ""
            src = item.get("source") or item.get("category") or ""
            suf = ""
            if url:
                suf = f" | {url[:120]}"
            lines.append(f"- [{sc}] {headline[:120]} ({src}) {rs}{suf}")
        body = "\n".join(lines)
        if len(body) > 4000:
            body = body[:3980] + "\n...[truncated]"
        await update.message.reply_text(body)

    async def _cmd_bias(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        from backend.strategy.ict_strategy import ICTStrategy

        if not isinstance(self._engine.strategy, ICTStrategy):
            await update.message.reply_text("Bias only available for ICT strategy")
            return
        await self.send_daily_bias_report(self._engine.strategy._states)  # type: ignore[arg-type]

    async def _cmd_reconnect(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        await self._engine.reconnect()
        await update.message.reply_text("Reconnect triggered")

    async def _cmd_report(self, update: Any, context: Any) -> None:
        if not self._engine:
            return
        risk = self._engine.risk.status_dict()
        positions = self._engine.trades.open_positions_dict()
        nf = self._engine.news_feed
        finnhub_disabled = not nf.is_finnhub_live()
        black = nf.active_blackouts() if not finnhub_disabled else []
        tops = nf.get_high_impact_news(60, 6) if not finnhub_disabled else []
        earn = nf.earnings_today() if not finnhub_disabled else []
        econ = nf.economic_events_snapshot() if not finnhub_disabled else {"events": [], "error": None}
        lines = [
            "Daily report",
            f"PnL: {risk['realized_pnl_day']:.2f}",
            f"Trades: {risk['trades_today']} / {risk['max_trades_per_day']}",
            f"Capital used: {risk['daily_capital_used']:.0f} / {risk['daily_capital_limit']:.0f}",
            f"Open positions: {len(positions)}",
            f"Blackouts active: {len(black)}",
        ]
        if positions:
            sym_line = ", ".join(p["symbol"] for p in positions[:24])
            lines.append(f"Symbols open: {sym_line}")
        if earn:
            lines.append(f"Earnings rows IB (watchlist-filter in feed): {len(earn)}")
        if econ.get("error"):
            lines.append(f"Econ cal: {econ['error']}")
        if tops:
            lines.append("Top impact news (cached):")
            for p in tops[:4]:
                lines.append(f"- {p.symbol} ({p.score}): {p.headline[:80]}")
        body = "\n".join(lines)
        if len(body) > 4000:
            body = body[:3980] + "\n...[truncated]"
        await update.message.reply_text(body)

