"""Risk management - sizing, daily caps, circuit breaker, signal validation."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pytz

from backend.config import AppConfig
from backend.db.database import Database
from backend.strategy.models import Direction, Signal

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


@dataclass
class SizingResult:
    """Output of position sizing."""

    shares: int
    risk_amount: float
    notional: float
    risk_per_share: float


@dataclass
class RiskState:
    """Mutable per-day risk state."""

    today: date
    daily_capital_used: float = 0.0
    realized_pnl_day: float = 0.0
    trades_today: int = 0
    circuit_broken: bool = False


class RiskManager:
    """Sizing, daily capital cap, daily loss circuit breaker."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        *,
        starting_equity: float = 100_000.0,
    ) -> None:
        self.config = config
        self.database = database
        self.equity = starting_equity
        self.state = RiskState(today=datetime.now(NY).date())

    # ---------- Daily lifecycle ---------- #

    def begin_day(self, today: Optional[date] = None) -> None:
        """Reset daily counters (call at 9:00 AM ET via the engine)."""

        self.state = RiskState(today=today or datetime.now(NY).date())
        logger.info("RiskManager: new trading day %s", self.state.today)

    # ---------- Validation ---------- #

    def validate_signal(self, signal: Signal) -> tuple[bool, Optional[str]]:
        """Hard pre-trade checks; returns (ok, reject_reason)."""

        if self.state.circuit_broken:
            return False, "circuit_breaker"
        if self.state.trades_today >= self.config.risk.max_trades_per_day:
            return False, "max_trades_per_day"

        risk = abs(signal.entry_price - signal.stop_loss)
        reward = abs(signal.take_profit - signal.entry_price)
        if risk <= 0:
            return False, "zero_risk"
        rr = reward / risk
        if rr < self.config.risk.min_rr_ratio:
            return False, f"rr_below_min({rr:.2f})"
        sl_width = risk / signal.entry_price
        if sl_width > self.config.risk.max_sl_width_pct:
            return False, f"sl_width_above_cap({sl_width:.4f})"
        return True, None

    # ---------- Sizing ---------- #

    def size_signal(self, signal: Signal) -> Optional[SizingResult]:
        """Compute share count and notional caps for a signal."""

        risk_amount = self.equity * self.config.risk.max_risk_per_trade
        risk_per_share = abs(signal.entry_price - signal.stop_loss)
        if risk_per_share <= 0:
            return None

        theoretical = math.floor(risk_amount / risk_per_share)
        notional_cap_shares = math.floor(20_000.0 / signal.entry_price)
        absolute_cap = 10_000
        shares = max(1, min(theoretical, notional_cap_shares, absolute_cap))
        notional = shares * signal.entry_price

        if not self.has_capital_capacity(notional):
            return None

        return SizingResult(
            shares=shares,
            risk_amount=risk_per_share * shares,
            notional=notional,
            risk_per_share=risk_per_share,
        )

    def has_capital_capacity(self, additional_notional: float) -> bool:
        """Return True if a new notional fits under the $100k daily cap."""

        cap = self.config.risk.daily_capital_limit
        return self.state.daily_capital_used + additional_notional <= cap

    def consume_capital(self, notional: float) -> None:
        """Reserve capital after an order is placed/filled."""

        self.state.daily_capital_used += notional

    # ---------- P&L tracking + circuit breaker ---------- #

    def register_trade_open(self) -> None:
        self.state.trades_today += 1

    def register_trade_close(self, realized_pnl: float) -> None:
        self.state.realized_pnl_day += realized_pnl
        if self.is_circuit_break_triggered():
            self.state.circuit_broken = True
            logger.warning(
                "Daily loss circuit breaker triggered: %.2f loss today",
                self.state.realized_pnl_day,
            )

    def is_circuit_break_triggered(self) -> bool:
        max_loss = self.equity * self.config.risk.max_daily_loss
        return self.state.realized_pnl_day <= -max_loss

    def check_circuit_breaker(self) -> bool:
        return self.state.circuit_broken

    # ---------- Inspection ---------- #

    def status_dict(self) -> dict:
        return {
            "equity": self.equity,
            "daily_capital_used": self.state.daily_capital_used,
            "daily_capital_limit": self.config.risk.daily_capital_limit,
            "realized_pnl_day": self.state.realized_pnl_day,
            "trades_today": self.state.trades_today,
            "circuit_broken": self.state.circuit_broken,
            "max_trades_per_day": self.config.risk.max_trades_per_day,
        }
