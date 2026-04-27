"""Read-only readiness for hourly market-news checks (no daemon started here)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import AppConfig, get_dotenv_load_warning
from .email_config_status import build_email_config_status
from .telegram_report_dedup import read_state


def _env(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def build_news_monitor_readiness(project_root: Path | str, cfg: AppConfig) -> dict[str, Any]:
    root = Path(project_root).resolve()
    nr = cfg.settings.news_reporting
    rep = cfg.settings.reports
    dedup = root / nr.dedup_store_relpath
    state = root / nr.state_relpath

    tg_ok = _env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID")
    email_status = build_email_config_status(cfg)
    providers = {
        "finnhub": _env("FINNHUB_API_KEY"),
        "fmp": _env("FMP_API_KEY"),
        "benzinga": _env("BENZINGA_API_KEY"),
        "polygon": _env("POLYGON_API_KEY"),
    }
    n_prov = sum(1 for v in providers.values() if v)

    blocking: list[str] = []
    if not nr.enabled:
        blocking.append("news_reporting.enabled is false in settings")
    if not rep.telegram_enabled and nr.telegram_enabled:
        blocking.append("reports.telegram_enabled is false; Telegram for reports disabled")
    if not tg_ok and nr.telegram_enabled:
        blocking.append("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")

    ready = not blocking
    st = read_state(state)
    last = st.get("last_result") or {}

    return {
        "news_reporting.enabled": bool(nr.enabled),
        "readiness": "ready" if ready else "not_ready",
        "hourly_market_news_check": bool(nr.hourly_market_news_check),
        "timezone": str(nr.timezone),
        "check_interval_minutes": int(nr.check_interval_minutes),
        "send_no_news_messages": bool(nr.send_no_news_messages),
        "min_market_moving_score": int(nr.min_market_moving_score),
        "telegram_configured": tg_ok,
        "dotenv_load_warning": get_dotenv_load_warning(),
        **email_status,
        "providers_configured": providers,
        "providers_count": n_prov,
        "dedup_store_path": str(dedup),
        "state_path": str(state),
        "ready": ready,
        "blocking_reasons": blocking,
        "next_suggested_schedule": (
            f"RTH weekdays: every {nr.check_interval_minutes} min call "
            f"`python3 -m bot.cli market-news-check --core-basket --market-moving-only --telegram` "
            f"via launchd/cron; default CLI uses --dry-run until you pass --no-dry-run."
        ),
        "last_telegram_status": last.get("telegram_status"),
        "last_items_scored": last.get("items_scored"),
    }
