"""Trade management - bracket order placement, trailing stops, scale-ins, EOD close."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import pytz
from ib_insync import IB, LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade

from backend.config import AppConfig
from backend.db.database import Database
from backend.db.models import ScaleInRecord, TradeRecord
from backend.execution.risk_manager import RiskManager, SizingResult
from backend.strategy.models import Direction, Signal, TradePosition

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")

CloseCallback = Callable[[TradePosition, dict[str, Any]], Awaitable[None]]


class TradeManager:
    """Manage bracket orders, trailing stops, scale-ins, and forced exits."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        ib: IB,
        *,
        risk: RiskManager,
        on_close: Optional[CloseCallback] = None,
    ) -> None:
        self.config = config
        self.database = database
        self.ib = ib
        self.risk = risk
        self.on_close = on_close
        self.positions: dict[str, TradePosition] = {}

    def set_on_close(self, callback: Optional[CloseCallback]) -> None:
        self.on_close = callback

    # ---------- Open ---------- #

    async def open_trade(
        self, signal: Signal, sizing: SizingResult
    ) -> Optional[TradePosition]:
        trade_id = str(uuid.uuid4())
        if signal.symbol in self.positions:
            logger.info("Already in position for %s; skipping new entry", signal.symbol)
            return None

        position = TradePosition(
            trade_id=trade_id,
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            direction=signal.direction,
            entry_price=signal.entry_price,
            entry_time=signal.timestamp,
            shares=sizing.shares,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_per_share=sizing.risk_per_share,
            risk_amount=sizing.risk_amount,
            fvg_top=signal.fvg_top,
            fvg_bottom=signal.fvg_bottom,
            entry_reason=signal.reason,
            entry_signal_score=signal.score,
        )
        self.positions[signal.symbol] = position

        await self.database.insert_trade(
            TradeRecord(
                trade_id=trade_id,
                symbol=signal.symbol,
                strategy=signal.strategy_name,
                direction=signal.direction.value,
                entry_price=signal.entry_price,
                entry_time=signal.timestamp,
                entry_shares=sizing.shares,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                entry_reason=signal.reason,
                entry_signal_score=signal.score,
                entry_fvg_top=signal.fvg_top,
                entry_fvg_bottom=signal.fvg_bottom,
                risk_amount=sizing.risk_amount,
            )
        )

        await self._submit_bracket(position)
        return position

    async def _submit_bracket(self, position: TradePosition) -> None:
        if not self.ib.isConnected():
            logger.warning(
                "IBKR not connected; bracket order for %s not submitted (paper mode)",
                position.symbol,
            )
            return
        contract = Stock(position.symbol, "SMART", "USD")
        try:
            await self.ib.qualifyContractsAsync(contract)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to qualify %s", position.symbol)
            return

        side = "BUY" if position.direction is Direction.LONG else "SELL"
        opp = "SELL" if side == "BUY" else "BUY"

        try:
            bracket = self.ib.bracketOrder(
                side,
                position.shares,
                limitPrice=round(position.entry_price, 2),
                takeProfitPrice=round(position.take_profit, 2),
                stopLossPrice=round(position.stop_loss, 2),
            )
            placed: list[Trade] = []
            for order in bracket:
                placed.append(self.ib.placeOrder(contract, order))
            if placed:
                position.parent_order_id = placed[0].order.orderId
                if len(placed) > 1:
                    position.take_profit_order_id = placed[1].order.orderId
                if len(placed) > 2:
                    position.stop_order_id = placed[2].order.orderId
            logger.info(
                "Bracket submitted: %s %s %d @ %.2f SL=%.2f TP=%.2f",
                side, position.symbol, position.shares,
                position.entry_price, position.stop_loss, position.take_profit,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to submit bracket for %s", position.symbol)

    # ---------- Update / management ---------- #

    async def on_price_update(
        self, symbol: str, price: float, now: datetime
    ) -> None:
        position = self.positions.get(symbol)
        if position is None or position.closed:
            return

        if position.direction is Direction.LONG:
            position.max_price_reached = max(
                position.max_price_reached or position.entry_price, price
            )
        else:
            position.min_price_reached = min(
                position.min_price_reached or position.entry_price, price
            )

        await self._maybe_move_stop(position, price)
        await self._maybe_update_trailing_stop(position, price)

    async def _maybe_move_stop(
        self, position: TradePosition, price: float
    ) -> None:
        if position.moved_to_breakeven:
            return
        current_r = position.current_r(price)
        if current_r >= self.config.ict.trailing_activation_r:
            new_stop = (
                position.entry_price + 0.01
                if position.direction is Direction.LONG
                else position.entry_price - 0.01
            )
            position.stop_loss = round(new_stop, 4)
            position.moved_to_breakeven = True
            position.trailing_activated = True
            position.trailing_stop = position.stop_loss
            logger.info(
                "%s: stop moved to breakeven %.4f (R=%.2f)",
                position.symbol, position.stop_loss, current_r,
            )
            await self._modify_stop_order(position)

    async def _maybe_update_trailing_stop(
        self, position: TradePosition, price: float
    ) -> None:
        if not position.trailing_activated:
            return
        distance = self.config.ict.trailing_distance_r * position.risk_per_share
        if position.direction is Direction.LONG:
            high = position.max_price_reached or price
            new_trail = high - distance
            if new_trail > (position.trailing_stop or 0):
                position.trailing_stop = round(new_trail, 4)
                position.stop_loss = position.trailing_stop
                await self._modify_stop_order(position)
        else:
            low = position.min_price_reached or price
            new_trail = low + distance
            if (
                position.trailing_stop is None
                or new_trail < position.trailing_stop
            ):
                position.trailing_stop = round(new_trail, 4)
                position.stop_loss = position.trailing_stop
                await self._modify_stop_order(position)

    async def _modify_stop_order(self, position: TradePosition) -> None:
        if not self.ib.isConnected() or position.stop_order_id is None:
            return
        # ib_insync exposes .openTrades() to find the live order
        for trade in self.ib.openTrades():
            if trade.order.orderId == position.stop_order_id:
                trade.order.auxPrice = round(position.stop_loss, 2)
                self.ib.placeOrder(trade.contract, trade.order)
                logger.debug(
                    "%s: modified stop order %d to %.2f",
                    position.symbol, position.stop_order_id, position.stop_loss,
                )
                return

    # ---------- Scale-in ---------- #

    async def maybe_scale_in(
        self, position: TradePosition, price: float, now: datetime
    ) -> Optional[int]:
        if position.scale_in_count >= self.config.ict.max_scale_ins:
            return None
        current_r = position.current_r(price)
        if current_r < self.config.ict.scale_in_threshold_r:
            return None

        add_shares = max(1, int(position.shares * 0.5))
        notional_add = add_shares * price
        if not self.risk.has_capital_capacity(notional_add):
            logger.info(
                "Scale-in skipped: daily notional cap for %s (+%.0f notional)",
                position.symbol,
                notional_add,
            )
            return None

        position.scale_in_count += 1

        if self.ib.isConnected():
            try:
                contract = Stock(position.symbol, "SMART", "USD")
                await self.ib.qualifyContractsAsync(contract)
                action = "BUY" if position.direction is Direction.LONG else "SELL"
                self.ib.placeOrder(contract, MarketOrder(action, add_shares))
            except Exception:  # noqa: BLE001
                logger.exception("Scale-in for %s failed", position.symbol)

        record = ScaleInRecord(
            trade_id=position.trade_id,
            price=price,
            shares=add_shares,
            time=now,
            reason=f"+{current_r:.2f}R scale-in #{position.scale_in_count}",
            time_unix=int(now.timestamp()),
        )
        await self.database.insert_scale_in(record)
        position.scale_in_records.append(
            {
                "price": price,
                "shares": add_shares,
                "time": now.isoformat(),
                "reason": record.reason,
            }
        )
        position.shares += add_shares
        logger.info(
            "%s scaled-in +%d @ %.2f (count=%d)",
            position.symbol, add_shares, price, position.scale_in_count,
        )
        return add_shares

    # ---------- Close ---------- #

    def _fallback_exit_price(self, position: TradePosition) -> float:
        if position.direction is Direction.LONG:
            return float(position.max_price_reached or position.entry_price)
        return float(position.min_price_reached or position.entry_price)

    async def _snapshot_market_price(self, contract: Stock) -> Optional[float]:
        try:
            tickers = await self.ib.reqTickersAsync(
                contract, regulatorySnapshot=False
            )
        except Exception:  # noqa: BLE001
            logger.exception("Snapshot price failed for %s", contract.symbol)
            return None
        if not tickers:
            return None
        t = tickers[0]
        p = t.marketPrice()
        if p is not None and not (isinstance(p, float) and math.isnan(p)):
            return float(p)
        for cand in (t.last, t.close):
            if cand is not None and not (
                isinstance(cand, float) and math.isnan(cand)
            ):
                return float(cand)
        return None

    async def force_close_all(self, *, reason: str = "force_close_eod") -> None:
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            if position.closed:
                continue
            await self._market_close(position, reason=reason)

    async def manual_close(self, symbol: str) -> bool:
        position = self.positions.get(symbol)
        if position is None or position.closed:
            return False
        await self._market_close(position, reason="manual")
        return True

    async def _market_close(
        self, position: TradePosition, *, reason: str
    ) -> None:
        exit_price = self._fallback_exit_price(position)

        if self.ib.isConnected():
            try:
                contract = Stock(position.symbol, "SMART", "USD")
                await self.ib.qualifyContractsAsync(contract)
                snap = await self._snapshot_market_price(contract)
                if snap is not None:
                    exit_price = snap
                elif exit_price == position.entry_price:
                    logger.warning(
                        "No snapshot for %s force-close; using entry as exit (weak PnL)",
                        position.symbol,
                    )
                action = "SELL" if position.direction is Direction.LONG else "BUY"
                self.ib.placeOrder(contract, MarketOrder(action, position.shares))
            except Exception:  # noqa: BLE001
                logger.exception("Force close for %s failed", position.symbol)
                return
        await self.finalize_close(position, exit_price=exit_price, reason=reason)

    async def finalize_close(
        self,
        position: TradePosition,
        *,
        exit_price: float,
        reason: str,
        exit_time: Optional[datetime] = None,
    ) -> None:
        if position.closed:
            return
        position.closed = True
        exit_dt = exit_time or datetime.now(NY)
        if position.direction is Direction.LONG:
            pnl = (exit_price - position.entry_price) * position.shares
            r = (
                (exit_price - position.entry_price) / position.risk_per_share
                if position.risk_per_share
                else 0.0
            )
        else:
            pnl = (position.entry_price - exit_price) * position.shares
            r = (
                (position.entry_price - exit_price) / position.risk_per_share
                if position.risk_per_share
                else 0.0
            )
        holding_minutes = max(
            0, int((exit_dt - position.entry_time).total_seconds() / 60)
        )

        await self.database.update_trade_close(
            position.trade_id,
            exit_price=exit_price,
            exit_time=exit_dt,
            exit_shares=position.shares,
            exit_reason=reason,
            realized_pnl=pnl,
            realized_r=r,
            holding_minutes=holding_minutes,
            trailing_activated=position.trailing_activated,
            trailing_stop_final=position.trailing_stop,
            max_price_reached=(
                position.max_price_reached
                if position.direction is Direction.LONG
                else position.min_price_reached
            ),
        )

        self.positions.pop(position.symbol, None)
        if self.on_close:
            await self.on_close(
                position,
                {"pnl": pnl, "r": r, "reason": reason, "exit_price": exit_price},
            )

    # ---------- Inspection ---------- #

    def open_positions_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "trade_id": p.trade_id,
                "symbol": p.symbol,
                "direction": p.direction.value,
                "entry_price": p.entry_price,
                "shares": p.shares,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "trailing_activated": p.trailing_activated,
                "trailing_stop": p.trailing_stop,
                "moved_to_breakeven": p.moved_to_breakeven,
                "scale_in_count": p.scale_in_count,
                "entry_time": p.entry_time.isoformat(),
            }
            for p in self.positions.values()
            if not p.closed
        ]
