"""Ticker edge profile page (Prompt 13L-alt).

Read-only: loads ``data/edge_profiles/*-edge-profiles.json`` from disk.
No IBKR, no TWS, no order placement.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ._helpers import base_context

router = APIRouter()


@router.get("/edge", response_class=HTMLResponse, name="edge_page")
def edge_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="edge")
    rows = state.get_edge_profiles_view()
    ctx["edge_profiles"] = rows
    if rows:
        ctx["edge_profile_date"] = rows[0].profile_date
        ctx["edge_profiles_source"] = rows[0].source_path
    else:
        ctx["edge_profile_date"] = ""
        ctx["edge_profiles_source"] = None
    ip = state.get_intraday_paper_config()
    ctx["edge_config"] = {
        "edge_profile_enabled": ip.edge_profile_enabled,
        "unknown_edge_policy": ip.unknown_edge_policy,
        "unknown_edge_risk_multiplier": ip.unknown_edge_risk_multiplier,
        "allow_aggressive_without_edge_profile": ip.allow_aggressive_without_edge_profile,
    }
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=5)
    return request.app.state.templates.TemplateResponse(
        request, "edge.html", ctx
    )
