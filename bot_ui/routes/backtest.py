"""Backtest page (Prompt 13E).

Renders the latest ICT/SMC intraday backtest report written under
``data/backtests/intraday/`` plus a research-only form whose buttons
go through the LocalCommandRunner allowlist (``backtest-intraday-smc``,
``fetch-candles``, ``backtest-report``).

Strict invariants — must remain true forever:

* This module MUST NOT import :mod:`bot.broker`, :mod:`bot.ibkr_client`,
  :mod:`ib_async`, or any backtest engine module that pulls those in.
  Rendering ``/backtest`` must work with TWS offline.
* No order placement; no live trading toggles.
* Live ticks / IBKR fetches happen only via explicit allowlisted CLI
  commands triggered from the form (or the regular CLI).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..strategy_lab_context import get_catalog_and_selection
from ._helpers import base_context

router = APIRouter()


@router.get("/backtest", response_class=HTMLResponse, name="backtest_page")
def backtest_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    root = request.app.state.project_root
    cat, ssel = get_catalog_and_selection(root)
    summary = state.get_backtest_summary()
    be = cat.strategies.get(ssel.active_backtest_strategy)
    ctx = base_context(request, active="backtest")
    ctx.update(
        {
            "backtest": summary,
            "page_title": "Backtest (strategy-selected)",
            "strategy_ui_catalog": cat,
            "strategy_selection": ssel,
            "active_backtest_entry": be,
            "backtest_effective_ict": ssel.active_backtest_strategy
            in {"ict_smc_intraday_v1"},
            "recent_results": request.app.state.command_queue.list_recent(limit=5),
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "backtest.html", ctx
    )
