"""CLI-driven market news fetch, score, dedup, optional Telegram. Never places orders."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..journal import Journal
from ..news.providers.stub_providers import FinnhubProvider, FmpProvider
from ..notifications.telegram import send_telegram_message
from .market_moving_score import score_market_moving
from .telegram_formatters import format_market_moving_telegram, ny_session_label
from .telegram_report_dedup import (
    STATUS_FAILED_DELIVERY,
    STATUS_FAILED_PROVIDER,
    STATUS_SENT,
    STATUS_SKIPPED_DUPLICATE,
    STATUS_SKIPPED_MISSING_CREDENTIALS,
    STATUS_SKIPPED_NOT_MARKET_MOVING,
    check_duplicate,
    read_state,
    record_sent,
    write_state,
)


def _parse_time(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt = datetime.strptime(s[:19], fmt)  # noqa: DTZ007
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _within_lookback(published: str, *, lookback_minutes: int) -> bool:
    if lookback_minutes <= 0:
        return True
    dt = _parse_time(published)
    if dt is None:
        return True  # include; scoring may be conservative elsewhere
    now = datetime.now(timezone.utc)
    return dt >= now - timedelta(minutes=lookback_minutes)


def run_market_news_check(
    project_root: Path,
    cfg: AppConfig,
    journal: Journal | None,
    *,
    symbols: list[str],
    market_moving_only: bool = True,
    lookback_minutes: int = 90,
    min_score: int = 70,
    want_telegram: bool = False,
    want_email: bool = False,  # reserved; no spam path in v1
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch symbol + general market lines, score, optional single Telegram (best item)."""
    root = Path(project_root).resolve()
    nr = cfg.settings.news_reporting
    dedup_path = root / nr.dedup_store_relpath
    state_path = root / nr.state_relpath

    if not cfg.settings.reports.telegram_enabled or not nr.telegram_enabled:
        want_telegram = False
    if want_email and not cfg.settings.reports.email_enabled:
        want_email = False

    prov_status: dict[str, Any] = {}
    items_raw: list[dict[str, Any]] = []
    watch = frozenset(s.upper() for s in symbols)

    fh = FinnhubProvider()
    r1 = fh.fetch_market_news()
    prov_status["finnhub_market"] = {"status": r1.status, "detail": r1.detail}
    for h in r1.items:
        items_raw.append(
            {
                "title": h.title,
                "url": h.url,
                "symbol": h.symbol or "",
                "source": h.source,
                "published_utc": h.published_utc,
            }
        )
    r2 = fh.fetch_symbol_news(list(symbols)[:25])
    prov_status["finnhub_symbol"] = {"status": r2.status, "detail": r2.detail}
    for h in r2.items:
        items_raw.append(
            {
                "title": h.title,
                "url": h.url,
                "symbol": h.symbol or "",
                "source": h.source,
                "published_utc": h.published_utc,
            }
        )

    fmp = FmpProvider()
    r3 = fmp.fetch_symbol_news(list(symbols)[:15])
    prov_status["fmp_symbol"] = {"status": r3.status, "detail": r3.detail}
    for h in r3.items:
        items_raw.append(
            {
                "title": h.title,
                "url": h.url,
                "symbol": h.symbol or "",
                "source": h.source,
                "published_utc": h.published_utc,
            }
        )

    if not items_raw and not r1.items and not r2.items and not r3.items:
        st = STATUS_FAILED_PROVIDER
        for v in prov_status.values():
            if isinstance(v, dict) and v.get("status") == "skipped_missing_credentials":
                st = STATUS_SKIPPED_MISSING_CREDENTIALS
                break
        out = {
            "ok": True,
            "telegram_status": st,
            "email_status": "not_sent",
            "items_scored": 0,
            "best": None,
            "providers": prov_status,
            "dry_run": dry_run,
            "note": "No data from providers (missing keys or network).",
        }
        write_state(state_path, {"last_result": out})
        return out

    scored: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in items_raw:
        t = (row.get("title") or "").strip()
        if not t:
            continue
        key = t.lower()[:200]
        if key in seen:
            continue
        seen.add(key)
        if not _within_lookback(str(row.get("published_utc") or ""), lookback_minutes=lookback_minutes):
            continue
        sym = str(row.get("symbol") or "")
        sc = score_market_moving(t, symbol=sym, watchlist=watch)
        adj = sc.score
        if not (row.get("published_utc") or "").strip():
            adj = max(0, adj - 5)
        scored.append(
            {
                **row,
                "score": adj,
                "matched_terms": sc.matched_terms,
            }
        )

    scored.sort(key=lambda x: -int(x.get("score") or 0))
    best_for_json: dict[str, Any] | None = None
    winner: dict[str, Any] | None = None
    for row in scored:
        if int(row.get("score") or 0) >= int(min_score):
            winner = row
            best_for_json = dict(row)
            break
    if scored and not best_for_json:
        best_for_json = {
            "title": scored[0].get("title"),
            "score": scored[0].get("score"),
            "note": "below_threshold",
        }

    last_status: str = "telegram_not_requested"
    telegram_sent = False
    if want_telegram:
        if not winner:
            last_status = STATUS_SKIPPED_NOT_MARKET_MOVING
        elif not cfg.telegram.is_configured:
            last_status = STATUS_SKIPPED_MISSING_CREDENTIALS
        elif dry_run:
            last_status = "dry_run_would_send" if cfg.telegram.is_configured else STATUS_SKIPPED_MISSING_CREDENTIALS
        else:
            dc = check_duplicate(
                dedup_path,
                str(winner.get("title")),
                str(winner.get("url") or ""),
                str(winner.get("symbol") or ""),
                window_hours=nr.dedup_window_hours,
            )
            if dc.is_duplicate:
                last_status = STATUS_SKIPPED_DUPLICATE
            else:
                body = format_market_moving_telegram(
                    title=str(winner.get("title")),
                    tickers=str(winner.get("symbol") or "MARKET")[:32],
                    why_matters=", ".join(winner.get("matched_terms") or [])[:200],
                    score=int(winner.get("score") or 0),
                    session_label=ny_session_label(cfg),
                )
                try:
                    telegram_sent = bool(
                        send_telegram_message(body, cfg=cfg, journal=journal)
                    )
                except (OSError, TypeError, ValueError):
                    telegram_sent = False
                if telegram_sent:
                    last_status = STATUS_SENT
                    record_sent(dedup_path, dc.key)
                else:
                    last_status = STATUS_FAILED_DELIVERY

    out: dict[str, Any] = {
        "ok": True,
        "telegram_status": last_status,
        "telegram_delivered": telegram_sent,
        "email_status": "not_sent" if not want_email else "skipped",
        "items_scored": len(scored),
        "candidates": scored[:15],
        "best": best_for_json,
        "providers": prov_status,
        "dry_run": dry_run,
        "send_no_news_messages": bool(nr.send_no_news_messages),
        "min_score_used": int(min_score),
    }
    if out["telegram_status"] in (STATUS_SKIPPED_NOT_MARKET_MOVING, "telegram_not_requested") and not nr.send_no_news_messages:
        out.setdefault(
            "note",
            "no qualifying market-moving item or Telegram not requested; no Telegram spam",
        )

    prev = read_state(state_path)
    write_state(
        state_path,
        {
            "last_result": out,
            "last_qualifying": best_for_json,
            "previous": {
                "updated_utc": prev.get("updated_utc"),
                "last_result": prev.get("last_result"),
            },
        },
    )
    return out
