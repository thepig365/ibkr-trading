"""Paper Forex bracket submission + status snapshots (Strategy Lab ICT FX 1m test).

Uses CASH @ IDEALPRO. Never submits unless caller passes all gates.
Submit prices must appear in ``preflight_audit`` rounded fields (never raw floats only).
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from bot.config import AppConfig
from bot.ibkr_client import IBKRClient
from bot.ibkr_client_ids import FOREX_FETCH
from bot.ibkr_connection import ibkr_client_collision_message, with_ibkr_client_id

from .forex_chart_save import save_forex_trade_chart_png
from .orders_log import append_forex_order_event
from .pairs import FxPairSpec, ibkr_contract_args
from .telegram_fx import send_fx_telegram
from .ticks import decimal_price
from .trade_lifecycle import format_entry_telegram, try_mark_alert_sent

logger = logging.getLogger(__name__)


def _require_preflight_audit(preflight_audit: dict[str, Any]) -> dict[str, str]:
    if not isinstance(preflight_audit, dict):
        raise TypeError("preflight_audit dict required")

    rounded_entry = preflight_audit.get("rounded_entry")
    rounded_stop = preflight_audit.get("rounded_stop")
    rounded_target = preflight_audit.get("rounded_target")
    if not all(isinstance(x, str) and x.strip() for x in (rounded_entry, rounded_stop, rounded_target)):
        raise ValueError("preflight_audit must include rounded_entry/rounded_stop/rounded_target as strings")
    return {
        "re": rounded_entry.strip(),
        "rs": rounded_stop.strip(),
        "rt": rounded_target.strip(),
    }


def _classify_orders_post_submit(ib: Any, order_ids: list[int | None]) -> dict[str, Any]:
    """Inspect openTrades logs + statuses after bracket submit."""

    ids = {int(x) for x in order_ids if x is not None}
    error_codes: list[int] = []
    error_messages: list[str] = []
    statuses: list[dict[str, Any]] = []
    normalized: list[str] = []

    for tr in getattr(ib, "openTrades", lambda: [])() or []:
        oid = getattr(getattr(tr, "order", None), "orderId", None)
        if oid is None or int(oid) not in ids:
            continue
        for le in getattr(tr, "log", []) or []:
            ec = int(getattr(le, "errorCode", 0) or 0)
            if ec:
                error_codes.append(ec)
                error_messages.append(str(getattr(le, "message", "") or ""))

        ost = getattr(tr, "orderStatus", None)
        if ost is None:
            continue
        raw_st = str(getattr(ost, "status", "") or "").strip()
        statuses.append(
            {
                "order_id": int(oid),
                "status": raw_st,
                "filled": float(getattr(ost, "filled", 0.0) or 0.0),
                "remaining": getattr(ost, "remaining", None),
                "avg_fill_price": getattr(ost, "avgFillPrice", None),
            }
        )
        normalized.append(raw_st.upper())

    has_110 = 110 in error_codes
    has_135 = 135 in error_codes

    broker_reject_code = error_codes[-1] if error_codes else None
    broker_reject_message = error_messages[-1] if error_messages else None
    rejection_class = ""

    secondary_note = ""
    if has_135 and has_110:
        secondary_note = "secondary_parent_missing_after_child_reject"
    elif has_135:
        secondary_note = "possibly_parent_missing"

    if has_110:
        rejection_class = "rejected_invalid_tick"

    acceptance = "unknown_timeout"
    ok_fin = False

    if statuses:
        if has_110:
            acceptance = "broker_rejected"
            ok_fin = False
        elif any("FILLED" in s for s in normalized):
            acceptance = "filled"
            ok_fin = True
        elif any(s in {"SUBMITTED", "PRESUBMITTED"} for s in normalized) and not error_codes:
            acceptance = "accepted"
            ok_fin = True
        elif any(s == "CANCELLED" for s in normalized) and has_110:
            acceptance = "broker_rejected"
        elif any(s == "CANCELLED" for s in normalized):
            acceptance = "cancelled"
        elif error_codes:
            acceptance = "broker_rejected"
        elif any(s == "INACTIVE" for s in normalized):
            acceptance = "inactive"
        else:
            acceptance = "submitted_to_api"
            # Without errors, treat as workable until proven otherwise (short sleep window)
            if not error_codes:
                ok_fin = True

    return {
        "error_codes_seen": sorted(set(error_codes)),
        "error_messages_seen": error_messages,
        "child_status_snapshots": statuses,
        "broker_acceptance_status": acceptance,
        "broker_reject_code": broker_reject_code,
        "broker_reject_message": broker_reject_message,
        "rejection_class": rejection_class,
        "secondary_reject_note": secondary_note,
        "ok_fin": ok_fin,
    }


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
    journal: Any = None,
    preflight_audit: dict[str, Any],
) -> dict[str, Any]:
    """Place LMT bracket (no MKT) using **rounded prices from** ``preflight_audit`` only.

    Legacy ``entry``/``stop``/``target`` floats are ignored for routing; callers should pass
    preflight_audit from ``FxBracketPreflight.to_audit_dict()``.
    """

    d = direction.lower()
    if d not in ("long", "short"):
        return {"ok": False, "error": "direction_invalid"}

    rnd = _require_preflight_audit(preflight_audit)
    rq_e = rnd["re"]
    rq_s = rnd["rs"]
    rq_t = rnd["rt"]

    # Defensive parity (do not silently mix raw floats)
    if (
        decimal_price(rq_e) != decimal_price(preflight_audit.get("entry", rq_e))
        or decimal_price(rq_s) != decimal_price(preflight_audit.get("stop", rq_s))
        or decimal_price(rq_t) != decimal_price(preflight_audit.get("target", rq_t))
    ):
        return {"ok": False, "error": "preflight_audit_entry_mismatch"}

    api_entry = float(decimal_price(rq_e))
    api_stop = float(decimal_price(rq_s))
    api_target = float(decimal_price(rq_t))

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
        "trade_id": secrets.token_hex(16),
        "strategy_id": "ict_fx_1m_test",
        "pair": spec.display,
        "direction": d,
        "side": side,
        "units": qty,
        "original_entry": preflight_audit.get("original_entry"),
        "original_stop": preflight_audit.get("original_stop"),
        "original_target": preflight_audit.get("original_target"),
        "entry": rq_e,
        "stop": rq_s,
        "target": rq_t,
        "rounded_entry": rq_e,
        "rounded_stop": rq_s,
        "rounded_target": rq_t,
        "min_tick": preflight_audit.get("min_tick"),
        "min_tick_source": preflight_audit.get("min_tick_source"),
        "price_rounding_audit": preflight_audit.get("price_rounding_audit"),
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
        br = ib.bracketOrder(side, qty, api_entry, api_target, api_stop, tif=tif)
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
                        "broker_acceptance_status": "broker_rejected",
                        "rejection_class": "api_exception",
                        "broker_reject_message": str(exc),
                        "message": str(exc),
                        "order_ids": order_ids,
                    },
                )
                return {"ok": False, "error": str(exc), "order_ids": order_ids}

        if getattr(ib, "sleep", None):
            ib.sleep(2.5)

        post = _classify_orders_post_submit(ib, order_ids)
        statuses = post["child_status_snapshots"]
        ok_fin = bool(post["ok_fin"])
        acceptance = post["broker_acceptance_status"]

        rec = {
            **record_base,
            "phase": "submitted",
            "status": acceptance,
            "order_ids": order_ids,
            "child_status_snapshots": statuses,
            "api_submit_attempted": submit_ok,
            "submit_ok": submit_ok,
            "broker_acceptance_status": acceptance,
            "broker_reject_code": post.get("broker_reject_code"),
            "broker_reject_message": post.get("broker_reject_message"),
            "rejection_class": post.get("rejection_class") or None,
            "secondary_reject_note": post.get("secondary_reject_note") or None,
        }
        if post.get("rejection_class") == "rejected_invalid_tick":
            rec["status"] = "rejected_invalid_tick"

        append_forex_order_event(project_root, rec)
        tid = str(record_base["trade_id"])
        out_ok: dict[str, Any] = {
            "ok": ok_fin,
            "trade_id": tid,
            "order_ids": order_ids,
            "statuses": statuses,
            "broker_acceptance_status": acceptance,
            "broker_reject_code": post.get("broker_reject_code"),
            "broker_reject_message": post.get("broker_reject_message"),
            "rejection_class": post.get("rejection_class"),
            "secondary_reject_note": post.get("secondary_reject_note"),
        }
        if not ok_fin and post.get("rejection_class") == "rejected_invalid_tick":
            out_ok["error"] = "invalid_tick_price"
            out_ok["reject_reason"] = "invalid_tick_price"

        should_telegram = ok_fin and acceptance in {"accepted", "filled", "submitted_to_api"}
        # Never announce hard rejects / IBKR tick errors as successful entries.
        if should_telegram and post.get("rejection_class"):
            should_telegram = False

        if should_telegram and try_mark_alert_sent(project_root, kind="entry", trade_id=tid):
            rootp = Path(project_root).resolve()
            cp = save_forex_trade_chart_png(
                project_root,
                trade_id=tid,
                pair_slug=spec.slug.upper(),
                entry=api_entry,
                stop=api_stop,
                target=api_target,
            )
            if cp:
                append_forex_order_event(
                    project_root,
                    {
                        "phase": "chart_saved",
                        "trade_id": tid,
                        "chart_png_relpath": str(cp.relative_to(rootp)),
                    },
                )
            send_fx_telegram(
                project_root=project_root,
                cfg=cfg,
                journal=journal,
                throttle_key=None,
                body=format_entry_telegram(
                    trade_id=tid,
                    pair=spec.display,
                    direction=d,
                    entry=api_entry,
                    stop=api_stop,
                    target=api_target,
                    units=qty,
                    order_ids=order_ids,
                ),
            )
        return out_ok
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


__all__ = ["submit_forex_paper_bracket", "_classify_orders_post_submit"]
