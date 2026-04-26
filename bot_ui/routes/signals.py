"""Signals page — MTF SMC + ICT/SMC Intraday tabs (Prompt 13D).

Pure read path: the page reads JSON files written by ``bot.cli`` /
worker scans. It NEVER imports :mod:`bot.broker` or
:mod:`bot.ibkr_client`, so rendering ``/signals`` is safe even when
TWS is offline and the dynamic watchlist is empty.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()

# Two strategy tabs are rendered side-by-side on /signals. The
# ``strategy`` query parameter switches the active tab and is
# normalised here so a stray value never reaches the template.
_VALID_STRATEGY_TABS: frozenset[str] = frozenset({"mtf_smc", "ict_smc_intraday_v1"})


@router.get("/signals", response_class=HTMLResponse, name="signals_page")
def signals_page(
    request: Request,
    strategy: str = Query(
        default="mtf_smc",
        description="Active strategy tab: mtf_smc | ict_smc_intraday_v1",
    ),
) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="signals")
    tab = strategy if strategy in _VALID_STRATEGY_TABS else "mtf_smc"
    ctx["active_strategy"] = tab
    ctx["signals"] = state.signals()
    ctx["intraday_signals"] = state.intraday_signals()
    ep_rows = state.get_edge_profiles_view()
    ctx["edge_profile_by_symbol"] = {r.symbol: r for r in ep_rows}
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=5)
    return request.app.state.templates.TemplateResponse(request, "signals.html", ctx)
