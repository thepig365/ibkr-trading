"""Risk engine.

Stateless validator that decides whether a hypothetical trade may be
authorized. In the foundation milestone the engine never approves any
trade because `trading.enabled` is false by default - but the rules
themselves are encoded so future milestones cannot accidentally bypass
them.

`RiskDecision.allowed == True` is necessary but NOT sufficient to place
an order; `broker.place_order` enforces additional gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import AppConfig

SecType = Literal["STK", "OPT", "FUT", "CRYPTO", "CASH", "BOND"]
Side = Literal["BUY", "SELL", "SHORT"]


@dataclass
class TradeIntent:
    symbol: str
    sec_type: SecType
    side: Side
    quantity: float
    estimated_price: float


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)

    def with_reason(self, reason: str) -> "RiskDecision":
        self.reasons.append(reason)
        self.allowed = False
        return self


class RiskEngine:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        intent: TradeIntent,
        *,
        account_equity: float | None = None,
        open_positions_count: int = 0,
        reconciliation_passed: bool = True,
    ) -> RiskDecision:
        decision = RiskDecision(allowed=True)
        s = self.cfg.settings

        # 1. Master switch: trading must be explicitly enabled.
        if not s.trading.enabled:
            decision.with_reason("trading.enabled is false")

        # 2. Live trading hard block.
        if s.account.block_live_trading and s.account.mode != "paper":
            decision.with_reason(
                f"block_live_trading=true and account.mode={s.account.mode!r}"
            )

        # 3. Asset class allow-list.
        sec = intent.sec_type.upper()
        if sec == "OPT" and not s.trading.allow_options:
            decision.with_reason("options trading disabled")
        if sec == "CRYPTO" and not s.trading.allow_crypto:
            decision.with_reason("crypto trading disabled")
        if sec == "CASH" and not s.trading.allow_forex:
            decision.with_reason("forex trading disabled")

        # 4. Direction.
        if intent.side.upper() == "SHORT" and not s.trading.allow_shorting:
            decision.with_reason("shorting disabled")

        # 5. Reconciliation gate.
        if (
            s.risk.block_new_trades_if_reconciliation_fails
            and not reconciliation_passed
        ):
            decision.with_reason("reconciliation failed; new trades blocked")

        # 6. Position count cap.
        if open_positions_count >= s.risk.max_open_positions:
            decision.with_reason(
                f"open positions {open_positions_count} >= max_open_positions {s.risk.max_open_positions}"
            )

        # 7. Position sizing cap (only when we know account equity).
        if account_equity is not None and account_equity > 0:
            notional = abs(intent.quantity) * abs(intent.estimated_price)
            max_notional = account_equity * (s.risk.max_equity_per_position_pct / 100.0)
            if notional > max_notional:
                decision.with_reason(
                    f"notional {notional:.2f} exceeds max_equity_per_position "
                    f"{max_notional:.2f}"
                )

        # 8. Sanity.
        if intent.quantity <= 0:
            decision.with_reason("quantity must be positive")
        if intent.estimated_price <= 0:
            decision.with_reason("estimated_price must be positive")

        return decision
