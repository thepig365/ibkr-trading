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

    After a successful non-dry paper submission, ``mtf_paper`` may hold
    IB order ids and related metadata.
    """

    intent: TradeIntent
    dry_run: bool
    confirmed: bool
    decision: RiskDecision
    mtf_paper: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
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
        if self.mtf_paper is not None:
            d["mtf_paper"] = self.mtf_paper
        return d


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
        mtf_paper_bracket: bool = False,
    ) -> OrderTicket:
        """Validate and submit a paper order when all gates pass.

        MTF paper bracket: set ``mtf_paper_bracket=True`` and enable
        ``trading.mtf_paper_bracket_enabled``; manual confirmation is
        bypassed only in that case when
        ``mtf_paper_bypass_manual_confirmation`` is true.
        """
        s = self.cfg.settings
        if mtf_paper_bracket and not s.trading.mtf_paper_bracket_enabled:
            raise TradingDisabled(
                "mtf_paper_bracket=True requires trading.mtf_paper_bracket_enabled=true"
            )
        if mtf_paper_bracket:
            effective_dry_run = s.trading.mtf_paper_dry_run
            if dry_run is not None:
                effective_dry_run = bool(dry_run)
        else:
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

        bypass_manual = bool(
            mtf_paper_bracket
            and s.trading.mtf_paper_bracket_enabled
            and s.trading.mtf_paper_bypass_manual_confirmation
        )
        if s.trading.require_manual_confirmation and not confirmed and not bypass_manual:
            self._record_blocked(ticket, "manual confirmation missing")
            raise ManualConfirmationRequired(
                "require_manual_confirmation=true; pass confirmed=True explicitly."
            )

        if effective_dry_run:
            self._record_blocked(ticket, "dry-run")
            return ticket

        return self._submit_order(ticket, mtf_paper_bracket=mtf_paper_bracket)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _submit_order(
        self, ticket: OrderTicket, *, mtf_paper_bracket: bool = False
    ) -> OrderTicket:
        if mtf_paper_bracket:
            return self._submit_mtf_paper_bracket(ticket)
        # NEVER add generic ib.placeOrder() here without a code review
        # — only the MTF paper bracket path may submit.
        raise TradingDisabled(
            "Order submission is only implemented for trading.mtf_paper_bracket "
            "(set mtf_paper_bracket=True with qualified intent)."
        )

    def _submit_mtf_paper_bracket(self, ticket: OrderTicket) -> OrderTicket:
        """Paper + ib_async bracket. Long-only, ``stop < entry < take profit``."""
        s = self.cfg.settings
        if s.account.mode != "paper":
            raise LiveTradingBlocked("MTF paper bracket is only for account.mode=paper")
        intent = ticket.intent
        if (intent.side or "").upper() != "BUY":
            raise TradingDisabled("MTF paper bracket is long-only (BUY).")
        el = intent.entry_limit_price
        tp = intent.take_profit_price
        sl = intent.stop_loss_price
        if el is None or tp is None or sl is None:
            raise TradingDisabled(
                "MTF bracket needs entry_limit_price, take_profit_price, stop_loss_price."
            )
        if not (float(sl) < float(el) < float(tp)):
            raise TradingDisabled(
                "MTF long bracket: require stop < entry < take_profit (got "
                f"{sl}, {el}, {tp})"
            )
        try:
            from ib_async import Stock
        except Exception as exc:  # noqa: BLE001
            raise TradingDisabled(f"ib_async required for order submission: {exc}") from exc
        ib = self.client._ib
        if ib is None:
            raise TradingDisabled("IB is not connected (use IBKRClient.connect(readonly=False)).")
        contract = Stock(intent.symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise TradingDisabled(f"Could not qualify contract for {intent.symbol!r}.")
        c = qualified[0]
        qty = max(1, int(intent.quantity))
        br = ib.bracketOrder("BUY", float(qty), float(el), float(tp), float(sl))
        order_ids: list[int | None] = []
        for o in br:
            ib.placeOrder(c, o)
            order_ids.append(int(getattr(o, "orderId", 0) or 0) or None)
        logger.info("MTF paper bracket placeOrder: %s %s", intent.symbol, order_ids)
        detail = {
            "kind": "mtf_paper_bracket",
            "symbol": intent.symbol,
            "quantity": qty,
            "entry": float(el),
            "take_profit": float(tp),
            "stop_loss": float(sl),
            "order_ids": order_ids,
        }
        ticket.mtf_paper = detail
        if self.journal is not None:
            self.journal.record_open_order(
                {**detail, "dry_run": False},
                source="mtf_paper_bracket",
            )
        return ticket

    def _record_blocked(self, ticket: OrderTicket, reason: str) -> None:
        logger.warning("Order blocked (%s): %s", reason, ticket.as_dict())
        if self.journal is not None:
            self.journal.record_event(
                category="order_blocked",
                level="WARNING",
                message=reason,
                payload=ticket.as_dict(),
            )
