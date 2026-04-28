"""Human-readable labels for Strategy Lab UI (13UX-NEWS).

Trading logic and safety invariants are unchanged — this is presentation only.
"""

from __future__ import annotations

from typing import Any

# Intraday / scan signal categories
CATEGORY_HUMAN: dict[str, tuple[str, str, str]] = {
    "DAY_TRADE_READY_STRICT": (
        "Ready to test (strict mode)",
        "ICT/SMC strict criteria are satisfied on the scan bar.",
        "Still need a 1-minute trigger and all paper safety gates before any order.",
    ),
    "DAY_TRADE_READY_AGGRESSIVE": (
        "Ready to test (aggressive mode)",
        "ICT/SMC aggressive criteria are satisfied on the scan bar.",
        "Still need a 1-minute trigger and all paper safety gates before any order.",
    ),
    "WATCH_ONLY": (
        "Watching",
        "No complete tradable setup yet on this bar.",
        "Wait for higher-timeframe context and 5m/1m alignment per your rules.",
    ),
    "NO_SETUP": (
        "No complete trade signal yet",
        "The scan did not find a full ICT/SMC entry structure.",
        "Re-scan after structure develops or on the next session.",
    ),
    "INVALID_RISK": (
        "Blocked — risk / R:R",
        "Stop/target or R:R do not pass configured minimums.",
        "Adjust levels or wait for a cleaner structure.",
    ),
    "BLOCKED": (
        "Blocked",
        "Explicit block from scan or safety rules.",
        "Review the technical details and Journal for the exact reason.",
    ),
    "ERROR": (
        "Error",
        "The scan or evaluation reported an error for this row.",
        "Check logs and re-run the scan.",
    ),
}

# Common skip / loop reasons (substrings or keys)
_SKIP_REASON_HUMAN: dict[str, str] = {
    "waiting_for_1m_trigger": "Waiting for 1-minute entry trigger",
    "1m_trigger": "Waiting for 1-minute entry trigger",
    "structure_context_missing": "Higher-timeframe structure incomplete",
    "higher_timeframe": "Higher-timeframe structure incomplete",
    "no ready candidates": "No complete trade signal yet",
    "no_ready": "No complete trade signal yet",
    "daily": "Daily paper test budget already used for this type of action",
    "kill": "Kill switch (emergency stop) is on",
    "reconcil": "Paper reconciliation did not pass",
    "tws": "TWS/IB Gateway may be unavailable (check connection)",
    "ledger": "Daily test budget or ledger check blocked this",
}


def humanize_skip_reason(reason: str) -> str:
    """Map one engine skip string to plain language; keep technical meaning."""
    rs = (reason or "").strip()
    if not rs:
        return ""
    low = rs.lower()
    if "trading_allow_shorting" in low and "false" in low:
        return "Skipped because short selling is disabled in paper safety settings."
    if "cannot submit short bracket" in low:
        return "Skipped because a short bracket cannot be submitted with current safety settings."
    if "open order exists" in low and "refuse duplicate" in low:
        return (
            "Skipped because an open order already exists for this symbol. "
            "The engine avoids duplicate paper entries."
        )
    if "existing position" in low and "refuse duplicate" in low:
        return (
            "Skipped because this symbol already has a paper position. "
            "The one-position-per-symbol gate blocked a duplicate entry."
        )
    if "duplicate paper entry" in low:
        return (
            "Skipped as a duplicate paper entry — another open order or position "
            "already exists for this symbol."
        )
    for k, h in _SKIP_REASON_HUMAN.items():
        if k in low:
            return h
    return rs


def humanize_skip_reasons(reasons: list[str] | None) -> list[str]:
    if not reasons:
        return []
    out: list[str] = []
    for r in reasons:
        rs = (r or "").strip()
        if not rs:
            continue
        out.append(humanize_skip_reason(rs))
    return out


def humanize_signal_category(key: str | None) -> tuple[str, str, str]:
    if not key:
        return ("Unknown", "—", "—")
    return CATEGORY_HUMAN.get(
        key,
        (key, "See technical details for this category.", "—"),
    )


def human_bracket_integrity(integrity: str | None) -> str:
    s = (integrity or "").strip().lower()
    if s == "complete":
        return "Stop/target protection: complete"
    if s == "incomplete":
        return "Stop/target protection: incomplete"
    return integrity or "—"


def next_action_hint(
    *,
    kill_switch: bool,
    ready_readiness: bool,
    has_intraday_rows: bool,
    strict_or_aggr: int,
    budget_remaining: float | None,
    bracket_incomplete: bool,
) -> str:
    if kill_switch:
        return "Turn off the emergency stop (kill switch) before any paper test — or stay flat."
    if bracket_incomplete:
        return "Review the Journal: stop/target protection was incomplete. Do not add size until TWS order legs look correct."
    if budget_remaining is not None and budget_remaining <= 0:
        return "Wait for a new day (UTC) for a fresh paper test daily budget, or review ledger settings."
    if not ready_readiness:
        return "Run Research Report and Paper Readiness from the safe buttons when you are ready to check the engine."
    if not has_intraday_rows:
        return "Run an Intraday Scan to refresh candidates — still no research rows on disk."
    if strict_or_aggr == 0:
        return "No names are in 'ready' lists yet — the scan may be waiting for a 1m trigger or better structure."
    return "If you intend a paper test, use only the approved buttons on the Paper page and keep ICT/SMC + 1m trigger requirements in mind."


def safety_today_label(
    paper_act: dict[str, Any] | None,
    kill_switch: bool,
    reconcile_ok: bool | None,
) -> tuple[str, str]:
    """Returns (headline, detail)."""
    if kill_switch:
        return (
            "Blocked — emergency stop",
            "The kill switch is on. The engine will not start automatic paper runs.",
        )
    pa = paper_act or {}
    if pa.get("final_readiness") == "READY_FOR_PAPER_TEST":
        r = "Attention" if not reconcile_ok else "Safe"
        if r == "Attention" and reconcile_ok is False:
            return (
                "Attention needed",
                "Readiness says OK, but the last recorded reconcile did not pass — verify before a paper test.",
            )
        if reconcile_ok is False:
            return (
                "Attention needed",
                "Last paper reconcile did not pass. Fix or verify in TWS before testing.",
            )
        return (
            "Safe for planned checks",
            "Config points to paper mode with safety gates. Use approved buttons only.",
        )
    reasons = pa.get("blocking_reasons") or []
    if reasons:
        return (
            "Not ready for paper test",
            "; ".join(str(x) for x in reasons[:4]),
        )
    return ("Unknown", "Run Paper Activation Status from the Paper page.")


def trade_readiness_label(
    intraday: Any,
    loop: Any,
    ledger: dict[str, Any],
) -> tuple[str, str]:
    """Plain-language trade readiness (research / budget), not a trade recommendation."""
    rem = None
    try:
        rem = float(ledger.get("daily_remaining_notional_usd") or 0)
    except (TypeError, ValueError):
        rem = None
    if rem is not None and rem <= 0:
        return (
            "Blocked — daily test budget",
            "Today’s paper test budget (notional) is used up. Wait for a new day or check ledger rules.",
        )
    if intraday and not getattr(intraday, "is_empty", True):
        rs = (getattr(intraday, "ready_strict_symbols", None) or []) or []
        ra = (getattr(intraday, "ready_aggressive_symbols", None) or []) or []
        if rs or ra:
            return (
                "Names ready on last scan (research)",
                "Strict/aggressive ‘ready’ lists are non-empty — still need 1m trigger and paper gates to send anything.",
            )
    skipped = []
    if loop is not None and hasattr(loop, "skipped_reasons"):
        skipped = list(getattr(loop, "skipped_reasons") or [])
    if skipped:
        h = humanize_skip_reasons(skipped)
        return (
            "Waiting",
            h[0] if h else "The automatic paper run reported waiting reasons (see below).",
        )
    return (
        "No complete signal yet (last data)",
        "Run an intraday scan or wait for the next bar set — or check why rows are empty.",
    )
