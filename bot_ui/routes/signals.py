"""Signals (MTF SMC) page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/signals", response_class=HTMLResponse, name="signals_page")
def signals_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="signals")
    ctx["signals"] = state.signals()
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=5)
    return request.app.state.templates.TemplateResponse(request, "signals.html", ctx)
