"""Signals page — strategy tabs; read-only, no TWS on render."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from bot.strategy_ui import DEFAULT_STRATEGY_ID

from ..strategy_lab_context import get_catalog_and_selection
from ._helpers import base_context

router = APIRouter()


def _resolve_tab(
    request: Request,
    strategy_q: str | None,
) -> tuple[str, list[str], object | None, bool]:
    """Return (tab_id, warnings, ui_entry|None, scan_blocked)."""
    cat, sel = get_catalog_and_selection(request.app.state.project_root)
    warnings: list[str] = []
    if strategy_q and str(strategy_q).strip():
        q = str(strategy_q).strip()
        if q in cat.strategies:
            tab = q
        else:
            warnings.append(f"Unknown strategy {q!r} — using saved scan strategy.")
            tab = sel.active_scan_strategy
    else:
        tab = sel.active_scan_strategy
    if tab not in cat.strategies:
        warnings.append("Invalid saved strategy — using default.")
        tab = cat.default_strategy if cat.default_strategy in cat.strategies else DEFAULT_STRATEGY_ID
    entry = cat.strategies.get(tab)
    scan_blocked = entry is not None and (not entry.scan_enabled)
    return tab, warnings, entry, scan_blocked


@router.get("/signals", response_class=HTMLResponse, name="signals_page")
def signals_page(
    request: Request,
    strategy: str | None = Query(
        default=None,
        description="Strategy tab (optional; default from saved scan strategy).",
    ),
) -> HTMLResponse:
    state = request.app.state.state_store
    cat, sel = get_catalog_and_selection(request.app.state.project_root)
    tab, res_warn, ui_entry, scan_blocked = _resolve_tab(request, strategy)

    ctx = base_context(request, active="signals")
    ctx.update(
        {
            "active_strategy": tab,
            "active_strategy_entry": ui_entry,
            "strategy_ui_catalog": cat,
            "ui_strategy_list": list(cat.strategies.items()),
            "strategy_selection": sel,
            "strategy_resolve_warnings": res_warn + list(sel.last_warnings or []),
            "strategy_scan_blocked": scan_blocked,
            "signals": state.signals(),
            "intraday_signals": state.intraday_signals(),
            "edge_profile_by_symbol": {r.symbol: r for r in state.get_edge_profiles_view()},
            "recent_results": request.app.state.command_queue.list_recent(limit=5),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "signals.html", ctx)
