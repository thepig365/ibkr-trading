"""Paper Forex bracket submission + status snapshots (Strategy Lab ICT FX 1m test).

Uses CASH @ IDEALPRO. Never submits unless caller passes all gates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bot.config import AppConfig
from bot.ibkr_client import IBKRClient
from bot.ibkr_client_ids import FOREX_FETCH
from bot.ibkr_connection import ibkr_client_collision_message, with_ibkr_client_id

from .orders_log import append_forex_order_event
from .pairs import FxPairSpec, ibkr_contract_args

logger = logging.getLogger(__name__)


def submit_forex_paper_bracket(
    *,
    project_root: Path,
    cfg: AppConfig,
    spec: FxPairSpec,
    direction: str,
    units: float,
    entry: float,
    stop: float,
    target: float,
    order_ref_prefix: str,
    tif: str = "DAY",
) -> dict[str, Any]:
    """Place LMT bracket (no MKT) on paper account; return order ids + last known statuses."""

    d = direction.lower()
    if d not in ("long", "short"):
        return {"ok": False, "error": "direction_invalid"}

    side = "BUY" if d == "long" else "SELL"
    qty = abs(float(units))
    args = ibkr_contract_args(spec)

    attempted: list[int] = []
    last_exc: BaseException | None = None
    client: IBKRClient | None = None

    for step in range(3):
        cid = FOREX_FETCH + step
        attempted.append(cid)
        sub = with_ibkr_client_id(cfg, cid)
        cl = IBKRClient(sub)
        try:
            cl.connect(readonly=False, timeout=12.0)
            client = cl
            break
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if ibkr_client_collision_message(exc) and step < 2:
                continue
            raise

    if client is None:
        return {
            "ok": False,
            "error": repr(last_exc) if last_exc else "connect_failed",
            "attempted_client_ids": attempted,
        }

    record_base: dict[str, Any] = {
        "phase": "submit",
        "pair": spec.display,
        "direction": d,
        "side": side,
        "units": qty,
        "entry": entry,
        "stop": stop,
        "target": target,
        "order_ref_prefix": order_ref_prefix,
        "sec_type": args["sec_type"],
        "exchange": args["exchange"],
    }

    try:
        try:
            from ib_async import Contract  # type: ignore
        except Exception as exc:  # noqa: BLE001
            append_forex_order_event(
                project_root,
                {**record_base, "status": "error", "message": f"ib_async: {exc}"},
            )
            return {"ok": False, "error": f"ib_async: {exc}"}

        ib = client._ib
        if ib is None:
            append_forex_order_event(
                project_root, {**record_base, "status": "error", "message": "ib_none"}
            )
            return {"ok": False, "error": "ib_not_connected"}

        c_kw: dict[str, Any] = {
            "symbol": args["symbol"],
            "currency": args["currency"],
            "exchange": args["exchange"],
        }
        c_kw["secType"] = args["sec_type"]
        c = Contract(**c_kw)
        qualified = ib.qualifyContracts(c)
        if not qualified:
            append_forex_order_event(
                project_root,
                {**record_base, "status": "rejected", "message": "qualify_failed"},
            )
            return {"ok": False, "error": "qualify_failed"}

        c0 = qualified[0]
        br = ib.bracketOrder(side, qty, float(entry), float(target), float(stop), tif=tif)
        for o in br:
            o.tif = str(tif).upper()
            o.orderRef = f"{order_ref_prefix}_{spec.slug}"
        order_ids: list[int | None] = []
        submit_ok = False
        for o in br:
            try:
                ib.placeOrder(c0, o)
                submit_ok = True
                order_ids.append(int(getattr(o, "orderId", 0) or 0) or None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Forex bracket placeOrder exception: %s", exc)
                append_forex_order_event(
                    project_root,
                    {
                        **record_base,
                        "status": "api_error",
                        "message": str(exc),
                        "order_ids": order_ids,
                    },
                )
                return {"ok": False, "error": str(exc), "order_ids": order_ids}

        if getattr(ib, "sleep", None):
            ib.sleep(2.0)
        statuses: list[dict[str, Any]] = []
        for tr in getattr(ib, "openTrades", lambda: [])() or []:
            oid = getattr(getattr(tr, "order", None), "orderId", None)
            if oid in [x for x in order_ids if x is not None]:
                statuses.append(
                    {
                        "order_id": oid,
                        "status": str(
                            getattr(getattr(tr, "orderStatus", None), "status", "") or ""
                        ),
                        "filled": float(
                            getattr(getattr(tr, "orderStatus", None), "filled", 0.0) or 0.0
                        ),
                        "remaining": getattr(
                            getattr(tr, "orderStatus", None), "remaining", None
                        ),
                        "avg_fill_price": getattr(
                            getattr(tr, "orderStatus", None), "avgFillPrice", None
                        ),
                    }
                )

        final = "Submitted" if statuses else "pending_ack"
        rec = {
            **record_base,
            "phase": "submitted",
            "status": final,
            "order_ids": order_ids,
            "child_status_snapshots": statuses,
            "api_submit_attempted": True,
            "submit_ok": submit_ok,
        }
        append_forex_order_event(project_root, rec)
        return {"ok": True, "order_ids": order_ids, "statuses": statuses}
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


__all__ = ["submit_forex_paper_bracket"]
