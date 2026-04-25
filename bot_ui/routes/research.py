"""Research Intelligence Layer page (read-only).

Renders the latest report/instructions written by ``python -m bot.cli
research-report``. This route MUST NOT import :mod:`bot.ibkr_client`
or any provider module. The state store reads JSON files only; live
news/macro fetches happen exclusively via CLI buttons that go through
the LocalCommandRunner allowlist.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/research", response_class=HTMLResponse, name="research")
def research(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    summary = state.get_research_summary()
    ctx = base_context(request, active="research")
    ctx.update(
        {
            "research": summary,
            "page_title": "Research Intelligence",
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "research.html", ctx
    )
