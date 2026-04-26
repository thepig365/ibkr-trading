"""Dashboard page (command center — Prompt 13UI). Render path: files only, no TWS/IBKR."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.config import load_config
from bot.execution.intraday_paper_sizing import ledger_snapshot_for_status
from bot.paper_activation import build_paper_activation_status

from ._helpers import base_context

router = APIRouter()


def _stdout_after(recent: list[Any], *names: str) -> str | None:
    for r in recent:
        if not getattr(r, "accepted", True):
            continue
        req = getattr(r, "request", None)
        cmd = getattr(req, "command", "") if req else ""
        if cmd in names and getattr(r, "exit_code", None) == 0:
            out = (getattr(r, "stdout", None) or "")[:6000]
            if out.strip():
                return out
    return None


@router.get("/dashboard", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    root = request.app.state.project_root
    ctx = base_context(request, active="dashboard")
    paper_rep: dict[str, str | None] = {}
    if hasattr(state, "latest_paper_report_links"):
        paper_rep = state.latest_paper_report_links()  # type: ignore[assignment,union-attr]

    recent = request.app.state.command_queue.list_recent(limit=24)
    first_snap: dict[str, Any] = {}
    if hasattr(state, "get_first_paper_pass_snapshot"):
        first_snap = state.get_first_paper_pass_snapshot()  # type: ignore[union-attr]

    cfg = load_config(project_root=root)
    ip = cfg.settings.trading.intraday_paper
    ledger: dict[str, Any] = {}
    try:
        ledger = ledger_snapshot_for_status(cfg, ip)
    except (OSError, TypeError, ValueError):
        ledger = {}
    try:
        paper_act = build_paper_activation_status(cfg, probe_ibkr=False, journal=None)
    except (OSError, TypeError, ValueError):
        paper_act = {}

    ctx.update(
        {
            "account": state.account_summary(),
            "positions": state.positions(),
            "watchlist": state.watchlist(),
            "signals": state.signals(),
            "loop": state.loop_status(),
            "runtime": state.runtime_flags(),
            "paper_reports": paper_rep,
            "intraday": state.intraday_signals(),
            "edge_profiles": state.get_edge_profiles_view()[:8],
            "backtest": state.get_backtest_summary(),
            "intraday_loop": state.get_intraday_paper_loop_status(),
            "first_paper": first_snap,
            "paper_sizing_ledger": ledger,
            "paper_activation": paper_act,
            "recent_command_results": recent,
            "last_engine_stdout": _stdout_after(
                recent, "engine-status", "strategy-lab-engine-status"
            ),
            "last_ibkr_stdout": _stdout_after(recent, "ibkr-session-status"),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "dashboard.html", ctx)
