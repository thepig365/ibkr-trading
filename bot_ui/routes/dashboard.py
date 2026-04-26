"""Dashboard page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="dashboard")
    paper_rep: dict[str, str | None] = {}
    if hasattr(state, "latest_paper_report_links"):
        paper_rep = state.latest_paper_report_links()  # type: ignore[assignment,union-attr]
    ctx.update(
        {
            "account": state.account_summary(),
            "positions": state.positions(),
            "watchlist": state.watchlist(),
            "signals": state.signals(),
            "loop": state.loop_status(),
            "runtime": state.runtime_flags(),
            "paper_reports": paper_rep,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "dashboard.html", ctx)
