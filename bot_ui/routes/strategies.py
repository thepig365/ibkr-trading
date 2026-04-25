"""Strategy Registry page (read-only).

Renders the latest registry summary written by
``python -m bot.cli multi-strategy-scan`` or per-strategy
``strategy-scan``. This route MUST NOT import :mod:`bot.broker`,
:mod:`bot.ibkr_client`, or :mod:`ib_async`. The state store reads
JSON files only; live scans happen exclusively via CLI buttons that
go through the LocalCommandRunner allowlist.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/strategies", response_class=HTMLResponse, name="strategies")
def strategies(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    summary = state.get_strategy_registry_summary()
    ctx = base_context(request, active="strategies")
    ctx.update(
        {
            "strategy_registry": summary,
            "page_title": "Strategy Registry",
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "strategies.html", ctx
    )
