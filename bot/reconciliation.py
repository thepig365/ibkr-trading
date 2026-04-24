"""Reconciliation: cross-check broker state against local journal.

The reconciler is read-only by contract. It MUST never call any
broker write API.

It produces a `ReconciliationReport` with:
  * positions_without_stops: positions that have no protective stop
    order at the broker.
  * unknown_open_orders:    orders open at the broker that the local
    journal does not recognise.
  * missing_local_records:  symbols our journal expected to be open
    but the broker no longer reports.
  * passed: True iff all the above are empty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from .broker import Broker
from .ibkr_client import OpenOrderRow, PositionRow
from .journal import Journal

logger = logging.getLogger(__name__)

PROTECTIVE_ORDER_TYPES = {"STP", "STP LMT", "TRAIL", "TRAIL LIMIT", "MIT"}


@dataclass
class ReconciliationReport:
    positions_without_stops: list[str] = field(default_factory=list)
    unknown_open_orders: list[dict] = field(default_factory=list)
    missing_local_records: list[str] = field(default_factory=list)
    passed: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "positions_without_stops": self.positions_without_stops,
            "unknown_open_orders": self.unknown_open_orders,
            "missing_local_records": self.missing_local_records,
            "notes": self.notes,
        }


def _has_protective_stop(symbol: str, position: float, orders: Iterable[OpenOrderRow]) -> bool:
    """A protective stop is a STP/TRAIL order on the same symbol whose
    side is opposite to the position direction."""
    if position == 0:
        return True
    needed_action = "SELL" if position > 0 else "BUY"
    for o in orders:
        if o.symbol != symbol:
            continue
        if (o.order_type or "").upper() not in PROTECTIVE_ORDER_TYPES:
            continue
        if (o.action or "").upper() != needed_action:
            continue
        return True
    return False


def reconcile(broker: Broker, journal: Journal | None = None) -> ReconciliationReport:
    """Run a read-only reconciliation pass.

    `journal` is optional: when present, we additionally compare against
    locally-known orders/positions to detect drift. The function never
    writes anything other than appending an audit event to the journal.
    """
    report = ReconciliationReport()

    try:
        positions: list[PositionRow] = broker.get_positions()
        open_orders: list[OpenOrderRow] = broker.get_open_orders()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reconciliation failed to fetch broker state")
        report.passed = False
        report.notes.append(f"broker fetch error: {exc!r}")
        if journal is not None:
            journal.record_event(
                category="reconciliation",
                level="ERROR",
                message="broker fetch error",
                payload={"error": repr(exc)},
            )
        return report

    # 1. Positions without protective stops.
    for p in positions:
        if not _has_protective_stop(p.symbol, p.position, open_orders):
            report.positions_without_stops.append(p.symbol)

    # 2. Unknown open orders / 3. Missing local records.
    if journal is not None:
        local_perm_ids = journal.latest_local_open_order_perm_ids()
        for o in open_orders:
            if o.perm_id is not None and o.perm_id not in local_perm_ids:
                report.unknown_open_orders.append(o.to_dict())

        local_symbols = journal.latest_local_position_symbols()
        broker_symbols = {p.symbol for p in positions}
        for sym in sorted(local_symbols - broker_symbols):
            report.missing_local_records.append(sym)
    else:
        report.notes.append("no journal provided; broker-only checks performed")

    report.passed = (
        not report.positions_without_stops
        and not report.unknown_open_orders
        and not report.missing_local_records
    )

    if journal is not None:
        journal.record_event(
            category="reconciliation",
            level="INFO" if report.passed else "WARNING",
            message="PASS" if report.passed else "FAIL",
            payload=report.as_dict(),
        )

    return report
