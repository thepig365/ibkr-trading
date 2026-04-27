"""Telegram alerts for full-auto paper supervisor — blockers and actions. No secrets."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEDUP_RELPATH = "data/runtime/full_auto_blocker_dedup.json"
_DEDUP_WINDOW_MINUTES_DEFAULT = 120


# Canonical blocker codes (aligned with prompt PART C)
BLOCKER_TWS_NOT_LISTENING = "tws_not_listening"
BLOCKER_IBKR_FAILED = "ibkr_connection_failed"
BLOCKER_NOT_PAPER = "not_paper_account"
BLOCKER_RECONCILE = "paper_reconcile_failed"
BLOCKER_BUDGET = "daily_budget_zero"
BLOCKER_KILL = "kill_switch_active"
BLOCKER_STRATEGY = "wrong_active_strategy"
BLOCKER_LIVE = "live_trading_enabled"
BLOCKER_MARKET = "market_orders_enabled"
BLOCKER_BRACKET = "bracket_safety_failed"
BLOCKER_WINDOW = "outside_session_window"
BLOCKER_PREFLIGHT = "preflight_failed"
BLOCKER_BROKER = "broker_error"
BLOCKER_NO_READY = "no_ready_signal"
BLOCKER_INCOMPLETE = "bracket_incomplete"

_ACTION_HINTS: dict[str, str] = {
    BLOCKER_TWS_NOT_LISTENING: (
        "Open TWS/IB Gateway (paper), enable API, and ensure the port in config is listening."
    ),
    BLOCKER_IBKR_FAILED: (
        "Check TWS API settings, client id, and that the paper account is logged in."
    ),
    BLOCKER_NOT_PAPER: "Set account.mode to paper in settings; never use live for this flow.",
    BLOCKER_RECONCILE: "Run paper reconcile from Strategy Lab; fix account/API until clean.",
    BLOCKER_BUDGET: "Wait for next session or review daily cap / ledger; do not delete ledger data.",
    BLOCKER_KILL: "Remove data/KILL_SWITCH or use UI to clear kill switch when safe.",
    BLOCKER_STRATEGY: "Set active paper strategy to ict_smc_intraday_v1 in Strategy UI.",
    BLOCKER_LIVE: "Keep live_trading_allowed false and block_live_trading true.",
    BLOCKER_MARKET: "Keep market_orders_allowed false (LIMIT brackets only).",
    BLOCKER_BRACKET: "Fix bracket config: stop and target must be present for every entry.",
    BLOCKER_WINDOW: "Wait for the configured NY session window.",
    BLOCKER_PREFLIGHT: "Read blocker text and fix configuration or files noted.",
    BLOCKER_BROKER: "Check TWS order log and bracket legs (paper).",
    BLOCKER_INCOMPLETE: "In TWS: verify parent, stop, and target; cancel orphans if needed.",
    BLOCKER_NO_READY: "No ICT/SMC + MTF+1m ready signal — this is not an error, just no trade.",
}


def format_blocker_telegram(
    *,
    blocker_code: str,
    detail: str,
    ny_hhmm: str,
    action_hint: str | None = None,
) -> str:
    """HTML-safe, concise. No PII, no API keys."""
    d = (detail or "").strip()[:400]
    act = (action_hint or _ACTION_HINTS.get(blocker_code) or "").strip()
    act_line = f"<b>Action needed:</b> {act}\n" if act else ""
    return (
        f"⚠️ <b>Paper engine blocked</b> — {ny_hhmm} NY\n"
        f"<b>Reason:</b> {blocker_code}\n"
        f"{d}\n"
        f"{act_line}"
        f"<i>No trading action was taken. Paper only.</i>"
    )


def _dedup_key(blocker_code: str, detail: str) -> str:
    h = hashlib.sha256(
        f"{(blocker_code or '').lower()}|{(detail or '')[:200]}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{blocker_code}:{h}"


def _load_dedup(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    m = raw.get("blockers")
    return dict(m) if isinstance(m, dict) else {}


def _save_dedup(path: Path, m: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"blockers": m, "updated_utc": _now_utc_iso()},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def should_send_blocker_now(
    project_root: Path,
    *,
    blocker_code: str,
    detail: str,
    dedup_minutes: int = _DEDUP_WINDOW_MINUTES_DEFAULT,
) -> bool:
    """Resend if key new or last sent older than dedup window."""
    path = (Path(project_root) / DEDUP_RELPATH).resolve()
    key = _dedup_key(blocker_code, detail)
    m = _load_dedup(path)
    if key not in m:
        return True
    try:
        last = m[key]
        prev = datetime.fromisoformat(last.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = (now - prev).total_seconds() / 60.0
        return delta >= float(dedup_minutes)
    except (TypeError, ValueError, OSError):
        return True


def record_blocker_sent(project_root: Path, *, blocker_code: str, detail: str) -> None:
    path = (Path(project_root) / DEDUP_RELPATH).resolve()
    m = _load_dedup(path)
    m[_dedup_key(blocker_code, detail)] = _now_utc_iso()
    if len(m) > 5000:
        m = dict(list(m.items())[-2000:])
    _save_dedup(path, m)


def send_blocker_telegram_if_configured(
    cfg: Any,
    journal: Any,
    *,
    project_root: Path,
    blocker_code: str,
    human_detail: str,
    ny_hhmm: str,
) -> bool:
    """Return True if send attempted and succeeded."""
    if not getattr(cfg, "telegram", None) or not cfg.telegram.is_configured:
        return False
    if not should_send_blocker_now(project_root, blocker_code=blocker_code, detail=human_detail):
        return False
    from .notifications import send_telegram_message  # noqa: PLC0415

    body = format_blocker_telegram(
        blocker_code=blocker_code,
        detail=human_detail,
        ny_hhmm=ny_hhmm,
    )
    try:
        send_telegram_message(body, cfg=cfg, journal=journal)
        record_blocker_sent(project_root, blocker_code=blocker_code, detail=human_detail)
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("blocker telegram failed: %s", exc, exc_info=True)
        return False


def format_engine_started_telegram(*, session: str) -> str:
    s = (session or "full").strip()
    return (
        f"🟢 <b>Full-auto paper engine started</b> — session <code>{s}</code>\n"
        f"Strategy: ICT/SMC Intraday · Paper only · LIMIT brackets · no market orders"
    )


def format_engine_stopped_telegram(*, reason: str = "normal") -> str:
    return f"⏹ <b>Full-auto paper engine stopped</b> — {reason[:200]}\n<i>Paper only.</i>"


def format_paper_order_submitted_telegram(
    *,
    symbol: str,
    direction: str,
    notional_usd: float | None,
    bracket: str,
) -> str:
    n = f"${notional_usd:,.0f}" if notional_usd is not None else "n/a"
    return (
        f"📘 <b>Paper order submitted</b> — {symbol}\n"
        f"<b>Strategy:</b> ICT/SMC Intraday\n"
        f"<b>Direction:</b> {direction.title()}\n"
        f"<b>Notional:</b> {n}\n"
        f"<b>Protection:</b> {bracket}\n"
        f"<i>Engine note: Paper only. No live trading.</i>"
    )


def format_bracket_incomplete_urgent(*, symbol: str) -> str:
    return (
        f"🚨 <b>URGENT — bracket incomplete</b> — {symbol}\n"
        f"Verify parent / stop / target in TWS paper. <i>No live trading.</i>"
    )


def format_daily_cap_telegram(*, detail: str) -> str:
    return f"📊 <b>Daily cap reached</b> — paper\n{detail[:400]}\n<i>No further new risk today.</i>"


def format_eod_paper_summary_line(*, had_activity: bool, report_hint: str) -> str:
    return (
        f"📋 <b>EOD paper summary</b> — activity={had_activity}\n{report_hint[:500]}"
    )


__all__ = [
    "DEDUP_RELPATH",
    "BLOCKER_TWS_NOT_LISTENING",
    "BLOCKER_IBKR_FAILED",
    "BLOCKER_NOT_PAPER",
    "BLOCKER_RECONCILE",
    "BLOCKER_BUDGET",
    "BLOCKER_KILL",
    "BLOCKER_STRATEGY",
    "BLOCKER_LIVE",
    "BLOCKER_MARKET",
    "BLOCKER_BRACKET",
    "BLOCKER_WINDOW",
    "BLOCKER_PREFLIGHT",
    "format_blocker_telegram",
    "send_blocker_telegram_if_configured",
    "should_send_blocker_now",
    "record_blocker_sent",
    "format_engine_started_telegram",
    "format_engine_stopped_telegram",
    "format_paper_order_submitted_telegram",
    "format_bracket_incomplete_urgent",
    "format_daily_cap_telegram",
    "format_eod_paper_summary_line",
]
