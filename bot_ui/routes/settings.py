"""Settings page (placeholder + safety + allowlist viewer)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.config import load_config
from bot.data_lifecycle import data_dir_line, data_status
from bot.reports.report_email_status import load_report_email_status

from ..services.safety import (
    ALLOWED_COMMANDS,
    FORBIDDEN_ARG_TOKENS,
    FORBIDDEN_COMMAND_TOKENS,
)
from ._helpers import base_context

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse, name="settings_page")
def settings_page(request: Request) -> HTMLResponse:
    st = request.app.state.state_store
    root = request.app.state.project_root
    cfg = load_config(project_root=root)
    resend_ok = bool((os.environ.get("RESEND_API_KEY") or "").strip())
    t_ok = bool(
        (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    )
    ctx = base_context(request, active="settings")
    ctx.update(
        {
            "allowed_commands": ALLOWED_COMMANDS,
            "forbidden_command_tokens": sorted(FORBIDDEN_COMMAND_TOKENS),
            "forbidden_arg_tokens": sorted(FORBIDDEN_ARG_TOKENS),
            "runtime": st.runtime_flags(),
            "recent_results": request.app.state.command_queue.list_recent(limit=6),
            "reports_config": cfg.settings.reports,
            "news_reporting": cfg.settings.news_reporting,
            "telegram_env_configured": t_ok,
            "news_api_any": any(
                bool((os.environ.get(k) or "").strip())
                for k in (
                    "FINNHUB_API_KEY",
                    "FMP_API_KEY",
                    "BENZINGA_API_KEY",
                    "POLYGON_API_KEY",
                )
            ),
            "data_disk": data_status(root),
            "data_dir_line": data_dir_line,
            "report_email": load_report_email_status(
                root,
                resend_key_present=resend_ok,
                from_addr=(os.environ.get("REPORT_EMAIL_FROM") or "").strip(),
            ),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "settings.html", ctx)
