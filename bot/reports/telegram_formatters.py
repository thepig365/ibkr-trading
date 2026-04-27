"""Short Telegram bodies for reports — no raw JSON, phone-friendly."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import AppConfig


def format_market_moving_telegram(
    *,
    title: str,
    tickers: str,
    why_matters: str,
    score: int,
    session_label: str,
) -> str:
    """HTML parse_mode body; caller ensures cfg uses HTML."""
    t = html.escape((title or "")[:500])
    tk = html.escape((tickers or "—")[:200])
    wm = html.escape((why_matters or "Heuristic market-moving match.")[:400])
    sess = html.escape(session_label)
    return (
        f"🚨 <b>Market-moving news</b> — {sess}\n"
        f"<b>Ticker(s):</b> {tk}\n"
        f"<b>What:</b> {t}\n"
        f"<b>Why it may matter:</b> {wm}\n"
        f"<b>Score:</b> {int(score)}/100 (heuristic)\n"
        f"<b>Engine:</b> Does <b>not</b> trigger trades. "
        f"ICT/SMC + 1-minute trigger still required.\n"
        f"<b>UI:</b> Open <code>/reports</code> or <code>/research</code> for full context."
    )


def ny_session_label(cfg: AppConfig) -> str:
    tz = ZoneInfo(cfg.settings.news_reporting.timezone)
    return datetime.now(tz).strftime("%H:%M NY (%Y-%m-%d)")


__all__ = ["format_market_moving_telegram", "ny_session_label"]
