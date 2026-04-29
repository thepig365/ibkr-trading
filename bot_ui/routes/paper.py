"""Paper Trading page.

Important: this page does NOT place orders. It exposes:

* a paper-reconcile button (read-only check),
* a refresh-paper-account-state button (forces fresh snapshots),
* runtime toggles (kill switch, MTF auto paper enabled flag) implemented
  as filesystem flags read by :mod:`bot.auto_paper_loop` and
  :mod:`bot.auto_paper_mtf` running in a separate process.

Canonical paths (must match the worker; Telegram does not set kill via /kill, use this UI or CLI file):

* Kill switch  -> ``<project_root>/data/KILL_SWITCH``
* MTF auto on  -> ``<project_root>/data/runtime/mtf_auto_paper_enabled``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.auto_loop_readiness import build_auto_loop_readiness
from bot.automatic_paper_preflight import build_automatic_paper_engine_preflight
from bot.config import load_config
from bot.full_auto_paper_readiness import FULL_AUTO_STATE_RELPATH, build_full_auto_paper_readiness
from bot.forex.runner import build_forex_test_ui_context
from bot.launchd_full_auto_ui import build_background_runner_ui_context
from bot.broker_snapshot import load_broker_snapshot
from bot.tws_health_alerts import ui_alert_overlay
from bot.execution.intraday_paper_sizing import ledger_snapshot_for_status
from bot.paper_activation import (
    FIRST_PAPER_PASS_LAST_RELPATH,
    PAPER_READINESS_STATE_RELPATH,
    build_paper_activation_status,
)
from bot.journal_trade_charts_pipeline import journal_chart_cell
from bot.ux.paper_context import build_paper_page_ux

from ..strategy_lab_context import get_catalog_and_selection
from ..services.state_store import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    KILL_SWITCH_RELPATH,
    MTF_AUTO_PAPER_ENABLED_RELPATH,
)
from ._helpers import base_context

router = APIRouter()


def _read_json_optional(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


@router.get("/paper", response_class=HTMLResponse, name="paper_page")
def paper_page(request: Request) -> HTMLResponse:
    state = request.app.state.state_store
    ctx = base_context(request, active="paper")
    root = request.app.state.project_root
    cat, ssel = get_catalog_and_selection(root)
    p_entry = cat.strategies.get(ssel.active_paper_strategy)
    ctx["strategy_ui_catalog"] = cat
    ctx["strategy_selection"] = ssel
    ctx["active_paper_entry"] = p_entry
    cfg = load_config(project_root=root)
    ctx["paper_activation"] = build_paper_activation_status(cfg, probe_ibkr=False, journal=None)
    ctx["paper_readiness_snapshot"] = _read_json_optional(
        cfg.absolute(PAPER_READINESS_STATE_RELPATH)
    )
    ctx["first_paper_last_snapshot"] = _read_json_optional(
        cfg.absolute(FIRST_PAPER_PASS_LAST_RELPATH)
    )
    ctx["account"] = state.account_summary()
    ctx["positions"] = state.positions()
    ctx["loop"] = state.loop_status()
    ctx["runtime"] = state.runtime_flags()
    # Prompt 13F: intraday paper bracket section. Both reads are pure file
    # I/O — the UI render must NEVER connect to IBKR / TWS.
    ctx["intraday_paper_config"] = state.get_intraday_paper_config()
    ctx["intraday_paper_loop"] = state.get_intraday_paper_loop_status()
    ep_rows = state.get_edge_profiles_view()
    ctx["edge_profile_by_symbol"] = {r.symbol: r for r in ep_rows}
    ip = cfg.settings.trading.intraday_paper
    ctx["paper_sizing_ledger"] = ledger_snapshot_for_status(cfg, ip)
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=8)
    jv = state.get_journal_view(limit=30, view_filter="all", symbol="")
    ctx["latest_paper_orders"] = jv.paper_orders
    first_row = jv.paper_orders[0] if jv.paper_orders else None
    trade_href = None
    if first_row is not None and getattr(first_row, "trade_id", ""):
        trade_href = f"/trades/{first_row.trade_id}"
    ctx["paper_ux"] = build_paper_page_ux(
        max_notional_per_order_usd=float(ip.max_notional_per_order_usd),
        max_daily_notional_usd=float(ip.max_daily_notional_usd),
        market_orders_allowed=bool(ip.market_orders_allowed),
        paper_activation=ctx["paper_activation"],
        kill_switch=bool(ctx["runtime"].kill_switch_active),
        paper_sizing_ledger=ctx.get("paper_sizing_ledger"),
        intraday_loop=ctx["intraday_paper_loop"],
        first_journal_row=first_row,
        latest_trade_review_href=trade_href,
    )
    ctx["latest_journal_chart_cell"] = (
        journal_chart_cell(root, first_row) if first_row is not None else None
    )
    try:
        ctx["auto_loop_readiness"] = build_auto_loop_readiness(
            root, cfg, None, probe_ibkr=False
        )
    except (OSError, TypeError, ValueError):
        ctx["auto_loop_readiness"] = {
            "readiness": "Not ready",
            "readiness_reason": "auto_loop_readiness: check failed (see logs)",
            "next_safe_action": "run_readiness_check",
        }
    try:
        ctx["automatic_paper_engine"] = build_automatic_paper_engine_preflight(
            cfg, None, probe_ibkr=False
        )
    except (OSError, TypeError, ValueError):
        ctx["automatic_paper_engine"] = {"ok": False, "blockers": ["preflight error"]}
    try:
        ctx["full_auto_readiness"] = build_full_auto_paper_readiness(
            root, cfg, None, probe_ibkr=False, session="full", ui_safe=True
        )
    except (OSError, TypeError, ValueError):
        ctx["full_auto_readiness"] = {
            "ok": False,
            "status": "error",
            "blockers": ["readiness error"],
        }
    ctx["full_auto_supervisor_state"] = _read_json_optional(
        cfg.absolute(FULL_AUTO_STATE_RELPATH)
    )
    try:
        ctx["background_runner"] = build_background_runner_ui_context(root)
    except (OSError, TypeError, ValueError):
        r = root
        h = Path.home()
        ll = h / "Library" / "Logs" / "StrategyLab"
        ctx["background_runner"] = {
            "launchd_plist_in_user_dir": False,
            "launchd_plist_path_user": str(
                h / "Library" / "LaunchAgents" / "com.strategy-lab.full-auto-paper.plist"
            ),
            "repo_scripts_plist": str(r / "scripts" / "com.strategy-lab.full-auto-paper.plist"),
            "log_appended_supervisor": str(r / "logs" / "full_auto_paper_supervisor.log"),
            "log_launchd_stdout": str(r / "logs" / "launchd_full_auto.out.log"),
            "log_launchd_stderr": str(r / "logs" / "launchd_full_auto.err.log"),
            "wrapper_script_install_path": str(
                h / "Library" / "Application Support" / "StrategyLab" / "run_full_auto_paper_supervisor.sh"
            ),
            "library_logs_supervisor": str(ll / "full_auto_paper_supervisor.log"),
            "library_logs_launchd_out": str(ll / "launchd_full_auto.out.log"),
            "library_logs_launchd_err": str(ll / "launchd_full_auto.err.log"),
            "lock_file": str(r / "data" / "runtime" / "full_auto_paper_supervisor.lock"),
            "lock_file_launchd": str(
                h
                / "Library"
                / "Application Support"
                / "StrategyLab"
                / "full_auto_paper_supervisor.lock.run"
            ),
            "last_supervisor_state": {},
        }
    ctx["broker_snapshot"] = load_broker_snapshot(root)
    ctx["tws_health_ui"] = ui_alert_overlay(root)

    try:
        ctx["forex_readiness_card"] = build_forex_test_ui_context(root, cfg=cfg)
    except (OSError, TypeError, ValueError):
        ctx["forex_readiness_card"] = {}

    return request.app.state.templates.TemplateResponse(request, "paper.html", ctx)


@router.post("/paper/runtime/kill-switch", name="toggle_kill_switch")
def toggle_kill_switch(
    request: Request,
    enable: str = Form(default="off"),
) -> RedirectResponse:
    """Create or remove the canonical kill-switch file.

    Writes ``<project_root>/data/KILL_SWITCH`` — the same file checked by
    :func:`bot.auto_paper_mtf.is_kill_switch_active`. Telegram may use ``/resume``
    if `data/KILL_SWITCH` exists; ``/kill`` is not used from Telegram (use this route).
    The UI never places or cancels orders here.
    """
    project_root = request.app.state.project_root
    target = (project_root / KILL_SWITCH_RELPATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if (enable or "").strip().lower() in {"1", "on", "true", "yes"}:
        target.write_text(
            f"{datetime.now(timezone.utc).isoformat()} via local UI /paper\n",
            encoding="utf-8",
        )
    else:
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
    return RedirectResponse(url="/paper", status_code=303)


@router.post("/paper/runtime/mtf-auto", name="toggle_mtf_auto")
def toggle_mtf_auto(
    request: Request,
    state: str = Form(default="off"),
) -> RedirectResponse:
    """Write the canonical MTF auto-paper flag file.

    Writes ``<project_root>/data/runtime/mtf_auto_paper_enabled`` with
    ``1`` or ``0``. Consumed by
    :func:`bot.auto_paper_mtf.is_runtime_mtf_auto_enabled` /
    :func:`bot.auto_paper_mtf.is_runtime_mtf_auto_disabled_explicit` and by
    the auto-paper loop. The UI never places orders here.
    """
    project_root = request.app.state.project_root
    target = (project_root / MTF_AUTO_PAPER_ENABLED_RELPATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    val = (state or "").strip().lower()
    if val in {"1", "on", "true", "yes"}:
        target.write_text("1\n", encoding="utf-8")
    else:
        target.write_text("0\n", encoding="utf-8")
    return RedirectResponse(url="/paper", status_code=303)


@router.post("/paper/runtime/intraday-auto", name="toggle_intraday_auto")
def toggle_intraday_auto(
    request: Request,
    state: str = Form(default="off"),
) -> RedirectResponse:
    """Write the canonical intraday auto-paper flag file (Prompt 13F).

    Writes ``<project_root>/data/runtime/intraday_auto_paper_enabled``
    with ``1`` (ON) or ``0`` (explicit OFF). Consumed by
    :func:`bot.execution.intraday_paper_execution.is_intraday_paper_runtime_enabled`
    and the intraday paper loop running in a separate process. The UI
    NEVER places orders here — it just toggles a runtime flag the worker
    polls.
    """
    project_root = request.app.state.project_root
    target = (project_root / INTRADAY_AUTO_PAPER_ENABLED_RELPATH).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    val = (state or "").strip().lower()
    if val in {"1", "on", "true", "yes"}:
        target.write_text("1\n", encoding="utf-8")
    else:
        target.write_text("0\n", encoding="utf-8")
    return RedirectResponse(url="/paper", status_code=303)
