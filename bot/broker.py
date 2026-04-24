"""Broker facade.

In the foundation milestone the broker exposes ONLY safe, read-only
operations. `place_order` exists as a stub purely so the safety gates
can be unit-tested; it always raises. The real implementation will
land in a later milestone after a code review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .ibkr_client import IBKRClient
from .journal import Journal
from .risk_engine import RiskDecision, RiskEngine, TradeIntent

logger = logging.getLogger(__name__)


class TradingDisabled(RuntimeError):
    """Raised when an order would be placed but trading is disabled."""


class LiveTradingBlocked(RuntimeError):
    """Raised when an order would route to a live account."""


class ManualConfirmationRequired(RuntimeError):
    """Raised when an order is attempted without operator confirmation."""


@dataclass
class OrderTicket:
    """Lightweight representation of a hypothetical order.

    No method on this object actually contacts the broker.
    """

    intent: TradeIntent
    dry_run: bool
    confirmed: bool
    decision: RiskDecision

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.intent.symbol,
            "sec_type": self.intent.sec_type,
            "side": self.intent.side,
            "quantity": self.intent.quantity,
            "estimated_price": self.intent.estimated_price,
            "dry_run": self.dry_run,
            "confirmed": self.confirmed,
            "allowed": self.decision.allowed,
            "reasons": self.decision.reasons,
        }


class Broker:
    """Read-mostly facade over the IBKR client.

    `place_order` is intentionally inert; it documents and enforces all
    invariants but never sends anything to TWS. Future milestones will
    replace the body of `_submit_order` once the strategy layer exists.
    """

    def __init__(
        self,
        cfg: AppConfig,
        client: IBKRClient,
        journal: Journal | None = None,
    ) -> None:
        self.cfg = cfg
        self.client = client
        self.journal = journal
        self.risk = RiskEngine(cfg)

    # ------------------------------------------------------------------
    # Read-only passthroughs
    # ------------------------------------------------------------------
    def get_account_summary(self):  # noqa: ANN201 - thin wrapper
        return self.client.get_account_summary()

    def get_positions(self):  # noqa: ANN201
        return self.client.get_positions()

    def get_open_orders(self):  # noqa: ANN201
        return self.client.get_open_orders()

    def get_executions(self):  # noqa: ANN201
        return self.client.get_executions()

    # ------------------------------------------------------------------
    # Order placement (DISABLED in this milestone)
    # ------------------------------------------------------------------
    def place_order(
        self,
        intent: TradeIntent,
        *,
        dry_run: bool | None = None,
        confirmed: bool = False,
        reconciliation_passed: bool = True,
        account_equity: float | None = None,
        open_positions_count: int = 0,
    ) -> OrderTicket:
        """Validate and (someday) submit an order.

        In the foundation milestone this method ALWAYS refuses to send
        anything. It is exposed only so that safety tests can verify
        the gates fire in the correct order.
        """
        s = self.cfg.settings
        effective_dry_run = s.trading.dry_run_default if dry_run is None else dry_run

        decision = self.risk.evaluate(
            intent,
            account_equity=account_equity,
            open_positions_count=open_positions_count,
            reconciliation_passed=reconciliation_passed,
        )
        ticket = OrderTicket(
            intent=intent,
            dry_run=effective_dry_run,
            confirmed=confirmed,
            decision=decision,
        )

        # Hard gate 1: live trading.
        if s.account.block_live_trading and s.account.mode != "paper":
            self._record_blocked(ticket, "live trading blocked")
            raise LiveTradingBlocked(
                f"Refusing order: account.mode={s.account.mode!r} with block_live_trading=true"
            )

        # Hard gate 2: trading master switch.
        if not s.trading.enabled:
            self._record_blocked(ticket, "trading disabled")
            raise TradingDisabled(
                "trading.enabled is false. Order placement is disabled in the "
                "system-foundation milestone."
            )

        # Hard gate 3: risk.
        if not decision.allowed:
            self._record_blocked(ticket, "risk rejected: " + "; ".join(decision.reasons))
            raise TradingDisabled("Risk engine rejected: " + "; ".join(decision.reasons))

        # Hard gate 4: manual confirmation.
        if s.trading.require_manual_confirmation and not confirmed:
            self._record_blocked(ticket, "manual confirmation missing")
            raise ManualConfirmationRequired(
                "require_manual_confirmation=true; pass confirmed=True explicitly."
            )

        # Hard gate 5: dry run is the default in this milestone.
        if effective_dry_run:
            self._record_blocked(ticket, "dry-run")
            return ticket

        # If we reach this branch, all gates have passed. The actual
        # submission path is intentionally unimplemented.
        return self._submit_order(ticket)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _submit_order(self, ticket: OrderTicket) -> OrderTicket:
        # NEVER add ib.placeOrder() here without a code review against
        # docs/safety-rules.md.
        raise TradingDisabled(
            "Order submission is not implemented in the foundation milestone."
        )

    def _record_blocked(self, ticket: OrderTicket, reason: str) -> None:
        logger.warning("Order blocked (%s): %s", reason, ticket.as_dict())
        if self.journal is not None:
            self.journal.record_event(
                category="order_blocked",
                level="WARNING",
                message=reason,
                payload=ticket.as_dict(),
            )
