"""Research Intelligence Layer page (read-only).

Renders the latest report/instructions written by ``python -m bot.cli
research-report``. This route MUST NOT import :mod:`bot.ibkr_client`
or any provider module. The state store reads JSON files only; live
news/macro fetches happen exclusively via CLI buttons that go through
the LocalCommandRunner allowlist.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.config import load_config
from bot.premarket.storage import find_latest_premarket_brief
from bot.reports.news_monitor_readiness import build_news_monitor_readiness
from bot.reports.report_email_status import load_report_email_status
from bot.reports.telegram_report_dedup import read_state

from ._helpers import base_context

router = APIRouter()


@router.get("/research", response_class=HTMLResponse, name="research")
def research(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    root = request.app.state.project_root
    summary = state.get_research_summary()
    pm = find_latest_premarket_brief(root)
    resend_ok = bool((os.environ.get("RESEND_API_KEY") or "").strip())
    pm_email = load_report_email_status(
        root,
        resend_key_present=resend_ok,
        from_addr=(os.environ.get("REPORT_EMAIL_FROM") or "").strip(),
    )
    cfg = load_config(project_root=root)
    mstate = read_state(root / cfg.settings.news_reporting.state_relpath)
    ctx = base_context(request, active="research")
    ctx.update(
        {
            "research": summary,
            "page_title": "Research Intelligence",
            "premarket_brief": pm,
            "premarket_email": pm_email,
            "news_monitor": build_news_monitor_readiness(root, cfg),
            "market_news_state": mstate,
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "research.html", ctx
    )
