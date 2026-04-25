"""Journal page (Prompt 13F PART E).

Renders an aggregated, read-only view of:

* every paper-bracket submission audited under
  ``data/paper_orders/*-intraday-paper-orders.jsonl``
* the latest intraday backtest trades CSV (``data/backtests/intraday/.../*trades.csv``)

Strict invariants:

* This route MUST NOT import :mod:`bot.broker`, :mod:`bot.ibkr_client`,
  :mod:`ib_async`, or any execution module — rendering ``/journal`` must
  work with TWS offline and with no paper-bracket logs at all.
* The page must NEVER expose a button that places, cancels, or modifies
  any order; only research / inspection links.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/journal", response_class=HTMLResponse, name="journal_page")
def journal_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    journal = state.get_journal_view(limit=200)
    ctx = base_context(request, active="journal")
    ctx.update(
        {
            "journal": journal,
            "page_title": "Trade Journal (paper + backtest)",
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "journal.html", ctx
    )
