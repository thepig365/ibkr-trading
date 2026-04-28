"""Generated reports index (read-only; Prompt 13UI)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from bot.config import load_config
from bot.data_lifecycle import data_dir_line, data_status
from bot.premarket.storage import find_latest_premarket_brief
from bot.reports.email_config_status import build_email_config_status
from bot.reports.news_monitor_readiness import build_news_monitor_readiness
from bot.reports.report_email_status import load_report_email_status
from bot.reports.report_hub_ui import build_report_hub_ui_context
from bot.full_auto_paper_readiness import FULL_AUTO_STATE_RELPATH
from bot.journal_trade_charts_pipeline import read_last_trade_chart_batch_summary
from bot.trade_ledger import build_trade_records, ledger_summary_counts
from bot.trade_reports import build_journal_analytics_for_project
from bot.reports.telegram_report_dedup import read_state
from bot.ux.humanize import humanize_skip_reason

from ..i18n import get_locale
from ..strategy_lab_context import get_catalog_and_selection
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
    ctx["news_reporting"] = cfg.settings.news_reporting
    ctx["news_monitor"] = build_news_monitor_readiness(root, cfg)
    ctx["market_news_state"] = read_state(
        root / cfg.settings.news_reporting.state_relpath
    )
    ecs = build_email_config_status(cfg)
    ctx["report_email"] = load_report_email_status(root, email_status=ecs)
    ctx["data_disk"] = data_status(root)
    ctx["data_dir_line"] = data_dir_line
    ctx["premarket_brief"] = find_latest_premarket_brief(root)
    cat, ssel = get_catalog_and_selection(root)
    ctx["strategy_ui_catalog"] = cat
    ctx["strategy_selection"] = ssel
    ctx["active_paper_entry"] = cat.strategies.get(ssel.active_paper_strategy)
    if ctx.get("backtest") and not getattr(ctx["backtest"], "is_empty", True):
        ctx["backtest_strategy_id"] = getattr(
            ctx["backtest"],
            "strategy_id",
            "ict_smc_intraday_v1",
        )
    else:
        ctx["backtest_strategy_id"] = ssel.active_backtest_strategy

    ctx["paper_audit_hint"] = ""
    pod = root / "data" / "paper_orders"
    if pod.is_dir():
        cands = sorted(pod.glob("*-intraday-paper-orders.jsonl"))
        if cands:
            try:
                ctx["paper_audit_hint"] = str(cands[-1].relative_to(root))
            except ValueError:
                ctx["paper_audit_hint"] = str(cands[-1])

    ctx["report_hub"] = build_report_hub_ui_context(root)

    jv = state.get_journal_view(limit=500, view_filter="all", symbol="")
    loc = get_locale(request)
    ctx["journal_trade_samples"] = jv.paper_orders[:6]
    ctx["journal_incomplete_rows"] = [
        r
        for r in jv.paper_orders
        if (r.bracket_integrity or "").strip().lower() == "incomplete"
    ][:6]
    skipped_pairs: list[tuple[str, str]] = []
    for r in jv.paper_orders:
        if not r.skipped_reasons:
            continue
        skipped_pairs.append(
            (r.symbol, humanize_skip_reason(r.skipped_reasons[0], locale=loc))
        )
        if len(skipped_pairs) >= 6:
            break
    ctx["journal_skipped_pairs"] = skipped_pairs
    ctx["jr_review_sent"] = next((r for r in jv.paper_orders if r.submitted), None)
    ctx["jr_review_skipped"] = next(
        (r for r in jv.paper_orders if r.skipped_reasons), None
    )
    ctx["jr_review_incomplete"] = next(
        (
            r
            for r in jv.paper_orders
            if (r.bracket_integrity or "").strip().lower() == "incomplete"
        ),
        None,
    )
    ctx["trade_chart_batch_summary"] = read_last_trade_chart_batch_summary(root)

    ledger_rows = build_trade_records(root)
    ctx["ledger_summary"] = ledger_summary_counts(ledger_rows, root)
    ctx["journal_analytics"] = build_journal_analytics_for_project(root).to_dict()
    ctx["ledger_latest_submitted"] = next(
        (r for r in ledger_rows if r.submitted_to_broker or r.raw_json.get("submitted")),
        None,
    )
    ctx["ledger_latest_open"] = next((r for r in ledger_rows if r.status_slug == "open"), None)
    ctx["ledger_latest_closed"] = next((r for r in ledger_rows if r.status_slug == "closed"), None)
    ctx["ledger_latest_skipped"] = next((r for r in ledger_rows if r.status_slug == "skipped"), None)

    fa = root / FULL_AUTO_STATE_RELPATH
    ctx["full_auto_supervisor_state"] = {}
    if fa.is_file():
        try:
            raw = json.loads(fa.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                ctx["full_auto_supervisor_state"] = raw
        except (OSError, json.JSONDecodeError, TypeError):
            ctx["full_auto_supervisor_state"] = {}

    return request.app.state.templates.TemplateResponse(request, "reports.html", ctx)
