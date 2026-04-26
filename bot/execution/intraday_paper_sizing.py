"""Paper-only quantity / notional caps for ICT/SMC intraday (Prompt 13K.3)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..config import AppConfig, IntradayPaperConfig


def normalize_intraday_paper_tif(raw: str | None) -> str:
    s = (raw or "DAY").strip().upper()
    if s not in {"DAY"}:
        raise ValueError(
            f"trading.intraday_paper.tif must be 'DAY' for now (got {raw!r})"
        )
    return s


def _utc_today_ledger() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_today_submitted_broker_notional_usd(
    cfg: AppConfig,
) -> tuple[float, str | None]:
    """Sum ``estimated_notional`` for today (UTC) where ``submitted_to_broker`` is true.

    On read/parse failure, returns a blocking outcome: ``(inf, message)`` so the
    caller can refuse to size against an unknown daily usage (fail-safe).
    """
    from .intraday_paper_execution import PAPER_ORDERS_DIR  # noqa: PLC0415

    day = _utc_today_ledger()
    p = Path(cfg.absolute(PAPER_ORDERS_DIR)) / f"{day}-intraday-paper-orders.jsonl"
    if not p.is_file():
        return 0.0, None
    used = 0.0
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return float("inf"), "audit_ledger_unavailable:json_parse_error"
            if not row.get("submitted_to_broker"):
                continue
            n = row.get("estimated_notional")
            if n is None:
                try:
                    n = float(row.get("quantity") or 0) * float(row.get("entry") or 0)
                except (TypeError, ValueError):
                    n = 0.0
            used += float(n or 0.0)
    except OSError as exc:
        return float("inf"), f"audit_ledger_unavailable:{type(exc).__name__}"
    return used, None


def apply_paper_sizing_caps(
    cfg: AppConfig,
    *,
    entry: float,
    risk_based_quantity: int,
    equity: float,
    per_share_risk: float,
    ip: IntradayPaperConfig,
) -> tuple[int, dict[str, Any], list[str]]:
    """Apply per-trade, daily, account %, and max-qty caps (Prompt 13K.3).

    Returns ``(final_qty, audit_dict, skip_reasons)`` — when *final_qty* < 1,
    *skip_reasons* is non-empty.
    """
    max_n = float(ip.max_notional_per_order_usd)
    max_d = float(ip.max_daily_notional_usd)
    pct = float(ip.max_equity_per_position_pct)
    max_q = int(ip.max_quantity_per_order)
    if entry <= 0 or not math.isfinite(entry):
        return 0, {}, ["invalid entry price for sizing"]
    if equity <= 0 or not math.isfinite(equity):
        return 0, {}, ["equity not positive for sizing caps"]
    if risk_based_quantity < 0:
        return 0, {}, ["invalid risk_based_quantity"]

    used, d_warn = read_today_submitted_broker_notional_usd(cfg)
    audit: dict[str, Any] = {
        "risk_based_quantity": int(risk_based_quantity),
        "per_trade_notional_cap_usd": max_n,
        "max_daily_notional_usd": max_d,
        "max_equity_per_position_pct": pct,
        "account_cap_pct": float(pct),
        "max_quantity_per_order": max_q,
        "entry_price": float(entry),
        "account_equity_for_sizing": float(equity),
        "daily_notional_ledger_warning": d_warn,
        "per_trade_notional_cap_quantity": None,
        "account_cap_notional": None,
        "account_cap_quantity": None,
        "daily_remaining_quantity": None,
        "final_quantity": None,
        "estimated_notional": None,
        "actual_risk_amount": None,
        "per_trade_cap_applied": False,
        "daily_cap_applied": False,
        "account_cap_applied": False,
        "quantity_cap_applied": False,
    }

    if math.isinf(used) and d_warn:
        return 0, audit, [f"quantity_below_min_after_daily_notional_cap:{d_warn}"]

    today_before = float(used) if not math.isinf(used) else 0.0
    audit["today_submitted_notional_usd_before"] = today_before

    if today_before >= max_d - 1e-6:
        audit["daily_remaining_notional_usd"] = 0.0
        return 0, audit, ["daily_notional_limit_reached"]

    daily_rem = max(0.0, max_d - today_before)
    audit["daily_remaining_notional_usd"] = float(daily_rem)
    if daily_rem < entry - 1e-9:
        return 0, audit, ["quantity_below_min_after_daily_notional_cap"]

    q_risk = int(risk_based_quantity)
    per_trade_notional_q = int(math.floor(max_n / entry))
    daily_rem_q = int(math.floor(daily_rem / entry))
    account_notional = equity * (pct / 100.0)
    acct_q = int(math.floor(account_notional / entry)) if account_notional > 0 else 0
    q_m = int(max(0, max_q))

    audit["per_trade_notional_cap_quantity"] = per_trade_notional_q
    audit["account_cap_notional"] = float(account_notional)
    audit["account_cap_quantity"] = acct_q
    audit["daily_remaining_quantity"] = daily_rem_q

    final = min(q_risk, per_trade_notional_q, daily_rem_q, acct_q, q_m)
    audit["per_trade_cap_applied"] = (final < q_risk) and (final == per_trade_notional_q)
    audit["daily_cap_applied"] = (final < q_risk) and (final == daily_rem_q)
    audit["account_cap_applied"] = (final < q_risk) and (final == acct_q)
    audit["quantity_cap_applied"] = (final < q_risk) and (final == q_m)
    if final < 0:
        final = 0
    else:
        final = int(final)

    est_notional = final * float(entry)
    act_risk = final * float(per_share_risk)
    audit["final_quantity"] = int(final)
    audit["estimated_notional"] = float(est_notional)
    audit["actual_risk_amount"] = float(act_risk)

    if final < 1:
        if per_trade_notional_q < 1 and q_risk >= 1:
            return 0, audit, ["quantity_below_min_after_10k_trade_cap"]
        if daily_rem_q < 1 and q_risk >= 1:
            return 0, audit, ["quantity_below_min_after_daily_notional_cap"]
        if acct_q < 1 and q_risk >= 1:
            return 0, audit, ["quantity_below_min_after_account_10pct_cap"]
        return 0, audit, ["quantity_below_min_after_notional_cap"]

    return int(final), audit, []


def ledger_snapshot_for_status(cfg: AppConfig, ip: IntradayPaperConfig) -> dict[str, Any]:
    """Read-only notional summary for ``intraday-paper-status`` and UI."""
    used, warn = read_today_submitted_broker_notional_usd(cfg)
    max_d = float(ip.max_daily_notional_usd)
    if math.isinf(used) and warn:
        u = max_d
        rem = 0.0
    else:
        u = float(used) if not math.isinf(used) else 0.0
        rem = max(0.0, max_d - u)
    return {
        "max_notional_per_order_usd": float(ip.max_notional_per_order_usd),
        "max_daily_notional_usd": max_d,
        "max_equity_per_position_pct": float(ip.max_equity_per_position_pct),
        "max_quantity_per_order": int(ip.max_quantity_per_order),
        "tif": normalize_intraday_paper_tif(ip.tif),
        "today_submitted_notional_usd": u,
        "daily_remaining_notional_usd": rem,
        "daily_notional_ledger_warning": warn,
    }
