"""Human-facing copy for the Paper Trading page (read-only, no IBKR in this module)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .humanize import human_bracket_integrity, humanize_skip_reasons

_RECON_OK = ("ok", "pass", "success", "clean")


def _reconcile_ok(reconciliation_status: str) -> bool | None:
    s = (reconciliation_status or "").strip().lower()
    if not s:
        return None
    return any(x in s for x in _RECON_OK) and "not" not in s and "fail" not in s


@dataclass
class PaperPageUX:
    """What the user sees at the top of /paper (plain language)."""

    can_paper_test: str  # "Yes" / "No" / "Not sure"
    can_paper_test_detail: str
    blockers: list[str] = field(default_factory=list)
    risk_bullets: list[str] = field(default_factory=list)
    latest_sent_to_tws: str | None = None
    latest_protection: str | None = None
    latest_broker_error: str | None = None
    latest_why_skipped: str | None = None
    to_dict: dict[str, Any] = field(default_factory=dict)


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def build_paper_page_ux(
    *,
    max_notional_per_order_usd: float,
    max_daily_notional_usd: float,
    market_orders_allowed: bool,
    paper_activation: dict[str, Any] | None,
    kill_switch: bool,
    paper_sizing_ledger: Any | None,
    intraday_loop: Any,
    first_journal_row: Any | None,
) -> PaperPageUX:
    pa = paper_activation or {}
    ready = pa.get("final_readiness") == "READY_FOR_PAPER_TEST"
    rem: float | None = None
    if paper_sizing_ledger is not None:
        try:
            rem = float(
                getattr(paper_sizing_ledger, "daily_remaining_notional_usd", None)  # type: ignore[arg-type]
                or 0
            )
        except (TypeError, ValueError):
            rem = None
    if rem is not None and rem < 0:
        rem = 0.0

    rstat = (getattr(intraday_loop, "reconciliation_status", "") or "").strip()
    rec_ok = _reconcile_ok(rstat)

    hard: list[str] = []
    if kill_switch:
        hard.append("Kill switch (emergency stop) is on.")
    if not ready:
        for b in (pa.get("blocking_reasons") or [])[:5]:
            hard.append(str(b))
        if not hard and not ready:
            hard.append("Paper activation is not in “ready for paper test” state.")
    if rem is not None and rem <= 0:
        hard.append("Today’s paper test budget (daily notional cap) is used up.")
    if rec_ok is False:
        hard.append("Last paper reconcile did not pass — check TWS before testing.")

    strict_n = int(getattr(intraday_loop, "strict_ready_count", 0) or 0)
    aggr_n = int(getattr(intraday_loop, "aggressive_ready_count", 0) or 0)
    skipped = list(getattr(intraday_loop, "skipped_reasons", None) or [])

    soft: list[str] = []
    if not getattr(intraday_loop, "is_empty", True) and strict_n + aggr_n == 0:
        hs = humanize_skip_reasons(skipped)
        if hs:
            soft.append(hs[0])
        else:
            soft.append(
                "No “ready” names on the last automatic run — may need a new scan or 1-minute trigger."
            )

    if hard:
        can = "No"
        detail = hard[0]
    elif soft:
        can = "Not sure"
        detail = soft[0] + " Other file-side gates are OK; TWS must be up when a broker command runs (this page cannot see TWS)."
    else:
        can = "Yes"
        detail = "File-side gates allow a paper test. Use only approved safe buttons; TWS must be running when you start a command that talks to the broker."

    all_blockers = hard + soft

    risk = [
        f"Max about ${max_notional_per_order_usd:,.0f} notional per bracket (per configured cap).",
        f"Max about ${max_daily_notional_usd:,.0f} notional per day (paper test budget).",
        "Paper account only — no live trading path from this lab.",
    ]
    if not market_orders_allowed:
        risk.append("Market orders disabled — brackets use limit entry + stop + target as configured.")

    sent = prot = be = why = None
    r = first_journal_row
    if r is not None:
        if _row_get(r, "submitted"):
            sent = "Sent to TWS — full bracket as intended."
        elif _row_get(r, "submitted_to_broker"):
            sent = "Some orders reached TWS, but the bracket may be incomplete — see Journal."
        else:
            sent = "Not sent to TWS (skipped or not submitted)."

        prot = human_bracket_integrity(_row_get(r, "bracket_integrity"))
        errs = _row_get(r, "broker_errors") or []
        codes = _row_get(r, "broker_error_codes") or []
        if errs:
            be = " ".join(str(x) for x in (errs if isinstance(errs, list) else [errs])[:2])
        elif codes:
            c = codes if isinstance(codes, (list, tuple)) else [codes]
            be = "Codes: " + ", ".join(str(x) for x in c[:4])
        sr = _row_get(r, "skipped_reasons")
        if sr:
            why = " ".join(
                humanize_skip_reasons(list(sr if isinstance(sr, list) else [sr]))[:3]
            )

    d = {
        "can_paper_test": can,
        "can_paper_test_detail": detail,
        "blockers": all_blockers,
        "risk_bullets": risk,
        "latest_sent_to_tws": sent,
        "latest_protection": prot,
        "latest_broker_error": be,
        "latest_why_skipped": why,
    }
    return PaperPageUX(
        can_paper_test=can,
        can_paper_test_detail=detail,
        blockers=all_blockers,
        risk_bullets=risk,
        latest_sent_to_tws=sent,
        latest_protection=prot,
        latest_broker_error=be,
        latest_why_skipped=why,
        to_dict=d,
    )
