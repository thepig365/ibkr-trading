"""Dashboard page (command center — Prompt 13UI). Render path: files only, no IBKR API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.auto_loop_readiness import build_auto_loop_readiness
from bot.automatic_paper_preflight import build_automatic_paper_engine_preflight
from bot.full_auto_paper_readiness import build_full_auto_paper_readiness
from bot.config import load_config
from bot.execution.intraday_paper_sizing import ledger_snapshot_for_status
from bot.paper_activation import build_paper_activation_status
from bot.premarket.storage import find_latest_premarket_brief
from bot.reports.email_config_status import build_email_config_status
from bot.reports.news_monitor_readiness import build_news_monitor_readiness
from bot.telegram_listener_ui import build_telegram_listener_ui_context
from bot.reports.report_hub_ui import build_report_hub_ui_context
from bot.reports.operational_hints import load_operational_hints
from bot.reports.report_email_status import load_report_email_status
from bot.reports.telegram_report_dedup import read_state
from bot.trade_reports import build_dashboard_trade_context
from bot.ux.dashboard_context import DashboardUX

from ..strategy_lab_context import get_catalog_and_selection
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
    try:
        auto_loop_readiness = build_auto_loop_readiness(
            root, cfg, None, probe_ibkr=False
        )
    except (OSError, TypeError, ValueError):
        auto_loop_readiness = {
            "readiness": "Not ready",
            "readiness_reason": "auto_loop_readiness: check failed (see logs)",
            "next_safe_action": "run_readiness_check",
        }
    try:
        automatic_paper_engine = build_automatic_paper_engine_preflight(
            cfg, None, probe_ibkr=False
        )
    except (OSError, TypeError, ValueError):
        automatic_paper_engine = {"ok": False, "blockers": ["preflight error"]}
    try:
        full_auto_readiness = build_full_auto_paper_readiness(
            root, cfg, None, probe_ibkr=False, session="full", ui_safe=True
        )
    except (OSError, TypeError, ValueError):
        full_auto_readiness = {"ok": False, "status": "error", "blockers": ["readiness error"]}

    op_hints = load_operational_hints(root)
    report_email = load_report_email_status(
        root, email_status=build_email_config_status(cfg)
    )
    premarket = find_latest_premarket_brief(root)
    mnews = read_state(root / cfg.settings.news_reporting.state_relpath)
    nmon = build_news_monitor_readiness(root, cfg)
    try:
        tg_listener_dash = build_telegram_listener_ui_context(root)
    except (OSError, TypeError, ValueError):
        tg_listener_dash = {"launchd_plist_installed": False, "running_hint": False}
    cat, ssel = get_catalog_and_selection(root)
    paper_s_entry = cat.strategies.get(ssel.active_paper_strategy)
    ux = DashboardUX.from_runtime(
        paper_act=paper_act,
        runtime=state.runtime_flags(),
        intraday=state.intraday_signals(),
        loop=state.loop_status(),
        ledger=ledger,
        intraday_loop=state.get_intraday_paper_loop_status(),
        first_paper=first_snap,
    )

    ctx.update(
        {
            "trader_dash": build_dashboard_trade_context(root),
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
            "op_hints": op_hints,
            "report_email": report_email,
            "reports_config": cfg.settings.reports,
            "premarket_brief": premarket,
            "premarket_config": cfg.settings.premarket_brief,
            "ux": ux.to_dict,
            "last_engine_stdout": _stdout_after(
                recent, "engine-status", "strategy-lab-engine-status"
            ),
            "last_ibkr_stdout": _stdout_after(recent, "ibkr-session-status"),
            "last_paper_reconcile_stdout": _stdout_after(
                recent, "paper-reconcile"
            ),
            "strategy_ui_catalog": cat,
            "strategy_selection": ssel,
            "active_paper_strategy_entry": paper_s_entry,
            "auto_loop_readiness": auto_loop_readiness,
            "automatic_paper_engine": automatic_paper_engine,
            "full_auto_readiness": full_auto_readiness,
            "market_news_state": mnews,
            "news_monitor": nmon,
            "report_hub": build_report_hub_ui_context(root),
            "telegram_command_listener_dash": tg_listener_dash,
        }
    )
    return request.app.state.templates.TemplateResponse(request, "dashboard.html", ctx)
