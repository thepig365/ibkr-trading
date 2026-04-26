"""Generated reports index (read-only; Prompt 13UI)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.config import load_config
from bot.data_lifecycle import data_status
from bot.premarket.storage import find_latest_premarket_brief
from bot.reports.report_email_status import load_report_email_status

from ._helpers import base_context

router = APIRouter()


@router.get("/reports", response_class=HTMLResponse, name="reports_page")
def reports_page(request: Request) -> HTMLResponse:
    """List latest report artifacts from disk. Does not call IBKR."""
    from bot.reports.report_paths import latest_glob_path  # noqa: PLC0415

    state = request.app.state.state_store
    root = request.app.state.project_root
    ctx = base_context(request, active="reports")
    ctx["recent_results"] = request.app.state.command_queue.list_recent(limit=6)

    pr: dict[str, str | None] = {}
    if hasattr(state, "latest_paper_report_links"):
        pr = state.latest_paper_report_links()  # type: ignore[assignment,union-attr]
    ctx["paper_reports"] = pr

    research = state.get_research_summary()
    ctx["research"] = research

    ctx["backtest"] = state.get_backtest_summary()
    ctx["edge_rows"] = state.get_edge_profiles_view()[:5]
    ctx["intraday"] = state.intraday_signals()
    ctx["first_paper"] = (
        state.get_first_paper_pass_snapshot()
        if hasattr(state, "get_first_paper_pass_snapshot")
        else {}
    )

    # Latest edge / backtest / scan files on disk (paths only)
    rdir = root / "data" / "edge_profiles"
    ctx["edge_json_path"] = str(latest_glob_path(rdir, "*-edge-profiles.json") or "")
    bdir = root / "data" / "backtests" / "intraday"
    ctx["backtest_json_path"] = str(
        latest_glob_path(bdir, "*-backtest-summary.json") or ""
    )
    sdir = root / "data" / "intraday_smc"
    ctx["scan_json_path"] = str(
        latest_glob_path(sdir, "*-watchlist-intraday-smc-summary.json") or ""
    )

    cfg = load_config(project_root=root)
    ctx["reports_config"] = cfg.settings.reports
    resend_ok = bool((os.environ.get("RESEND_API_KEY") or "").strip())
    ctx["report_email"] = load_report_email_status(
        root,
        resend_key_present=resend_ok,
        from_addr=(os.environ.get("REPORT_EMAIL_FROM") or "").strip(),
    )
    ctx["data_disk"] = data_status(root)
    ctx["premarket_brief"] = find_latest_premarket_brief(root)

    ctx["paper_audit_hint"] = ""
    pod = root / "data" / "paper_orders"
    if pod.is_dir():
        cands = sorted(pod.glob("*-intraday-paper-orders.jsonl"))
        if cands:
            try:
                ctx["paper_audit_hint"] = str(cands[-1].relative_to(root))
            except ValueError:
                ctx["paper_audit_hint"] = str(cands[-1])

    return request.app.state.templates.TemplateResponse(request, "reports.html", ctx)
