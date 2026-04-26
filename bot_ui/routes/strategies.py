"""Strategy Control Center (read + selection file; no TWS, no orders)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.strategy_ui import (
    load_strategy_ui_catalog,
    load_strategy_selection,
    save_strategy_selection,
    selection_from_mapping,
    validate_per_area,
)

from ..strategy_lab_context import get_catalog_and_selection
from ._helpers import base_context

router = APIRouter()

_AREAS = ("scan", "backtest", "edge", "paper")
_AREA_FIELDS = {
    "scan": "active_scan_strategy",
    "backtest": "active_backtest_strategy",
    "edge": "active_edge_strategy",
    "paper": "active_paper_strategy",
}


@router.get("/strategies", response_class=HTMLResponse, name="strategies")
def strategies_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    root: Path = request.app.state.project_root
    cat, sel = get_catalog_and_selection(root)
    summary = state.get_strategy_registry_summary()
    bt = state.get_backtest_summary()
    ep_rows = state.get_edge_profiles_view()
    edge_date = (ep_rows[0].profile_date if ep_rows else "") or ""
    by_key = {r.key: r for r in summary.strategies}
    table_rows: list[dict[str, Any]] = []
    for key in cat.all_ids():
        reg = by_key.get(key)
        u = cat.strategies.get(key)
        row: dict[str, Any] = {
            "id": key,
            "ui": u,
            "reg": reg,
            "last_backtest": "",
            "last_edge": "",
        }
        if u is not None and u.backtest_enabled and key == (bt.strategy_id or "ict_smc_intraday_v1") and not bt.is_empty:
            row["last_backtest"] = (bt.finished_at_utc or bt.started_at_utc or "")[:10]
        elif u is not None and u.backtest_enabled and key == "ict_smc_intraday_v1" and not bt.is_empty:
            row["last_backtest"] = (bt.finished_at_utc or "")[:10]
        if u is not None and u.edge_profile_enabled and key == "ict_smc_intraday_v1" and edge_date:
            row["last_edge"] = edge_date
        table_rows.append(row)

    # Active strategy = paper strategy (primary for “what runs on the desk”)
    ap = cat.strategies.get(sel.active_paper_strategy)
    ctx = base_context(request, active="strategies")
    ctx.update(
        {
            "strategy_registry": summary,
            "strategy_ui_catalog": cat,
            "strategy_selection": sel,
            "strategy_table_rows": table_rows,
            "backtest_ref": bt,
            "active_paper_entry": ap,
            "default_strategy": cat.default_strategy,
            "page_title": "Strategy Control Center",
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "strategies.html", ctx
    )


@router.post(
    "/strategies/selection",
    response_class=RedirectResponse,
    name="strategies_set_selection",
)
def strategies_set_selection(
    request: Request,
    area: str = Form(...),
    strategy_id: str = Form(...),
) -> RedirectResponse:
    """Persist selection; validation prevents paper_enabled=false for paper area."""
    root: Path = request.app.state.project_root
    cat = load_strategy_ui_catalog(root)
    cur = load_strategy_selection(root, catalog=cat)
    a = (area or "").strip().lower()
    if a not in _AREA_FIELDS:
        return RedirectResponse(
            url="/strategies?msg=invalid_area",
            status_code=303,
        )
    ok, _err = validate_per_area(cat, a, (strategy_id or "").strip())
    if not ok:
        return RedirectResponse(
            url="/strategies?msg=invalid_strategy",
            status_code=303,
        )
    field = _AREA_FIELDS[a]
    st, _ = selection_from_mapping(
        cat,
        {field: (strategy_id or "").strip()},
        current=cur,
    )
    save_strategy_selection(root, st, catalog=cat)
    return RedirectResponse(url="/strategies?saved=1", status_code=303)
