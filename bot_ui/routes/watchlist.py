"""Watchlist page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .watchlist_helpers import recent_command_hints_for_watchlist
from ._helpers import base_context

router = APIRouter()


@router.get("/watchlist", response_class=HTMLResponse, name="watchlist_page")
def watchlist_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="watchlist")
    ctx["watchlist"] = state.watchlist()
    recent = request.app.state.command_queue.list_recent(limit=8)
    ctx["recent_results"] = recent
    ctx["recent_cmd_hints"] = recent_command_hints_for_watchlist(recent)
    return request.app.state.templates.TemplateResponse(request, "watchlist.html", ctx)
