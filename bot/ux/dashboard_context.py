"""Computed human-facing dashboard copy (file-only; no IBKR in this module)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .humanize import (
    next_action_hint,
    safety_today_label,
    trade_readiness_label,
)


@dataclass
class DashboardUX:
    safety_headline: str
    safety_detail: str
    trade_readiness_headline: str
    trade_readiness_detail: str
    next_action: str
    to_dict: dict[str, Any]

    @staticmethod
    def from_runtime(
        *,
        paper_act: dict[str, Any] | None,
        runtime: Any,
        intraday: Any,
        loop: Any,
        ledger: dict[str, Any],
        intraday_loop: Any,
        first_paper: dict[str, Any] | None,
    ) -> "DashboardUX":
        kill = bool(getattr(runtime, "kill_switch_active", False))
        pa = paper_act or {}
        rec: bool | None = None
        if intraday_loop is not None and hasattr(intraday_loop, "reconciliation_status"):
            rs = (getattr(intraday_loop, "reconciliation_status", "") or "").lower()
            if rs:
                rec = "pass" in rs or "ok" in rs
        s_h, s_d = safety_today_label(pa, kill, rec)
        t_h, t_d = trade_readiness_label(intraday, loop, ledger)

        b_inc = bool(getattr(intraday_loop, "last_bracket_incomplete", False))
        strict_n = 0
        if intraday and not getattr(intraday, "is_empty", True):
            strict_n = len(
                (getattr(intraday, "ready_strict_symbols", None) or [])
            ) + len((getattr(intraday, "ready_aggressive_symbols", None) or []))
        rem: float | None = None
        try:
            rem = float(ledger.get("daily_remaining_notional_usd", 0))
        except (TypeError, ValueError):
            rem = None
        na = next_action_hint(
            kill_switch=kill,
            ready_readiness=pa.get("final_readiness") == "READY_FOR_PAPER_TEST",
            has_intraday_rows=bool(
                intraday
                and not getattr(intraday, "is_empty", True)
                and (getattr(intraday, "symbols_scanned", 0) or 0) > 0
            ),
            strict_or_aggr=strict_n,
            budget_remaining=rem,
            bracket_incomplete=b_inc,
        )
        d = {
            "safety_headline": s_h,
            "safety_detail": s_d,
            "trade_readiness_headline": t_h,
            "trade_readiness_detail": t_d,
            "next_action": na,
        }
        return DashboardUX(
            safety_headline=s_h,
            safety_detail=s_d,
            trade_readiness_headline=t_h,
            trade_readiness_detail=t_d,
            next_action=na,
            to_dict=d,
        )
