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
        items = await self._engine.news_feed.fetch_company_news(symbol, limit=5)
        if not items:
            await update.message.reply_text(f"No recent news for {symbol}")
            return
        lines = [f"{symbol}:"]
        for item in items:
            headline = item.get("headline") or item.get("summary") or "(no title)"
            lines.append(f"- {headline[:160]}")
        await update.message.reply_text("\n".join(lines))

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
        await update.message.reply_text(
            "Daily report\n"
            f"PnL: {risk['realized_pnl_day']:.2f}\n"
            f"Trades: {risk['trades_today']}\n"
            f"Capital used: {risk['daily_capital_used']:.0f} / {risk['daily_capital_limit']:.0f}\n"
            f"Open positions: {len(positions)}"
        )
