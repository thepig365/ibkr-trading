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

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..strategy_lab_context import get_catalog_and_selection
from ._helpers import base_context

router = APIRouter()


def _load_last_backtest_oneclick(root: object) -> dict[str, Any] | None:
    """Read ``data/runtime/last_backtest_oneclick.json`` if present. No IBKR."""
    p = Path(str(root)) / "data" / "runtime" / "last_backtest_oneclick.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _backtest_candle_coverage_preview(root: object) -> dict[str, Any] | None:
    """Local 1m cache coverage (core basket, last ~7 days). No IBKR."""
    try:
        from bot.backtests.candle_coverage import CORE_BASKET, check_candle_coverage
    except (ImportError, OSError, ValueError, TypeError):
        return None
    try:
        rpath = Path(str(root))
        end = date.today()
        start = end - timedelta(days=7)
        rep = check_candle_coverage(
            list(CORE_BASKET),
            start.isoformat(),
            end.isoformat(),
            timeframe="1min",
            project_root=rpath,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None
    rows: list[dict[str, Any]] = []
    for sym in sorted(rep.get("per_symbol", {})):
        p = rep["per_symbol"][sym]
        st = str(p.get("status") or "")
        lab = st.replace("_", " ").title()
        rows.append(
            {
                "symbol": sym,
                "status": st,
                "status_label": lab,
                "cached_start": p.get("cached_start"),
                "cached_end": p.get("cached_end"),
                "missing_n": len(p.get("missing_trading_days") or []),
                "action": p.get("recommended_action") or "",
            }
        )
    return {
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "report": rep,
        "rows": rows,
        "all_ready": bool(rep.get("will_backtest_be_complete")),
    }


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
            "candle_coverage_preview": _backtest_candle_coverage_preview(root),
            "last_backtest_oneclick": _load_last_backtest_oneclick(root),
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "backtest.html", ctx
    )
