"""Trade ledger / trade records UI (local files only, no broker)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from bot.journal_trade_charts_pipeline import ensure_trade_chart_if_possible
from bot.journal_trade_lookup import find_paper_order_payload_by_trade_id
from bot.trade_journal_chart import (
    candles_available_for_trade,
    generate_trade_journal_chart_png,
    trade_review_chart_png_path,
)
from bot.trade_ledger import (
    build_trade_records,
    chart_png_exists,
    find_trade_record,
    ledger_summary_counts,
    skipped_reason_human_for,
)

from ..i18n import get_locale
from ._helpers import base_context

router = APIRouter()


def _status_key(slug: str) -> str:
    return {
        "open": "trades.status_open",
        "closed": "trades.status_closed",
        "skipped": "trades.status_skipped",
        "rejected": "trades.status_rejected",
        "protection_incomplete": "trades.status_protection_incomplete",
        "pending": "trades.status_pending",
        "partial": "trades.status_partial",
        "unknown": "trades.status_unknown",
    }.get(slug, "trades.status_unknown")


def _close_reason_key(slug: str) -> str:
    """i18n key for normalized close_reason slug."""
    m = {
        "not_recorded": "trades.cr_not_recorded",
        "target_hit": "trades.cr_target_hit",
        "stop_hit": "trades.cr_stop_hit",
        "manual": "trades.cr_manual",
        "eod": "trades.cr_eod",
        "unknown": "trades.cr_unknown",
    }
    return m.get(slug, "trades.cr_unknown")


def _filter_ledger_rows(
    rows: list,
    *,
    view_filter: str,
    symbol: str,
    direction: str,
    chart_filter: str,
) -> list:
    """Server-side filtering for /trades (local data only)."""

    vf = (view_filter or "all").strip().lower()
    sym_u = symbol.strip().upper()
    dire = (direction or "all").strip().lower()
    cf = (chart_filter or "all").strip().lower()

    def ok(r: object) -> bool:
        if vf == "open" and getattr(r, "status_slug", "") != "open":
            return False
        if vf == "closed" and getattr(r, "status_slug", "") != "closed":
            return False
        if vf == "skipped" and getattr(r, "status_slug", "") != "skipped":
            return False
        if vf == "protection_incomplete" and getattr(r, "status_slug", "") != "protection_incomplete":
            return False
        if vf == "pending" and getattr(r, "status_slug", "") != "pending":
            return False

        rs = getattr(r, "symbol", "")
        if sym_u and (rs or "").upper() != sym_u:
            return False

        d = (getattr(r, "direction", "") or "").strip().lower()
        if dire in {"long", "short"} and d != dire:
            return False

        cs = getattr(r, "chart_status", "") or ""
        if cf == "yes" and cs != "available":
            return False
        if cf == "missing" and cs != "missing_candles":
            return False
        if cf == "no" and cs == "available":
            return False
        return True

    return [r for r in rows if ok(r)]


def _risk_reward_per_share(tr: object) -> tuple[float | None, float | None]:
    entry = getattr(tr, "entry_price", None)
    stop = getattr(tr, "stop_price", None)
    target = getattr(tr, "target_price", None)
    dire = (getattr(tr, "direction", None) or "").strip().lower()
    if entry is None or stop is None:
        risk = None
    elif dire == "short":
        risk = stop - entry
    else:
        risk = entry - stop
    if entry is None or target is None:
        reward = None
    elif dire == "short":
        reward = entry - target
    else:
        reward = target - entry
    return risk, reward


@router.get("/trades", response_class=HTMLResponse, name="trades_page")
def trades_page(
    request: Request,
    view_filter: str = Query("all", alias="filter"),
    symbol: str = Query(""),
    direction: str = Query("all"),
    chart_filter: str = Query("all", alias="chart"),
) -> HTMLResponse:
    root: Path = request.app.state.project_root
    rows_all = build_trade_records(root)
    counts = ledger_summary_counts(rows_all, root)
    loc = get_locale(request)
    filtered = _filter_ledger_rows(
        rows_all,
        view_filter=view_filter,
        symbol=symbol,
        direction=direction,
        chart_filter=chart_filter,
    )

    ctx = base_context(request, active="trades")
    ctx.update(
        {
            "ledger_rows": filtered,
            "ledger_counts": counts,
            "trade_rows_all_count": len(rows_all),
            "trade_status_key": _status_key,
            "close_reason_key": _close_reason_key,
            "skipped_human": lambda rec: skipped_reason_human_for(rec, locale=loc),
            "trade_review_chart_png_exists": lambda tid: chart_png_exists(root, tid),
            "ledger_candles_probe": lambda rec: candles_available_for_trade(root, rec.raw_json),
            "journal_filter": view_filter,
            "trades_symbol": symbol.strip().upper(),
            "journal_direction": (direction or "all").strip().lower(),
            "journal_chart_filter": (chart_filter or "all").strip().lower(),
        }
    )
    return request.app.state.templates.TemplateResponse(request, "trades.html", ctx)


@router.get("/trades/{trade_id}", response_class=HTMLResponse, name="trade_detail_page")
def trade_detail_page(
    request: Request,
    trade_id: str,
    preview_chart: int = Query(
        0,
        alias="preview_chart",
        ge=0,
        le=1,
        description="Regenerate PNG from local candles (no IBKR).",
    ),
    generated: int = Query(0, alias="generated", ge=0, le=1),
) -> HTMLResponse:
    root: Path = request.app.state.project_root
    tid = (trade_id or "").strip().lower()
    rec = find_trade_record(root, tid)
    if rec is None:
        ctx = base_context(request, active="trades")
        ctx["unknown_trade_id"] = tid
        return request.app.state.templates.TemplateResponse(
            request,
            "trade_not_found.html",
            ctx,
            status_code=404,
        )
    loc = get_locale(request)
    chart_notice: str | None = None
    if preview_chart == 1:
        out = generate_trade_journal_chart_png(root, tid, force=True, locale=loc)
        if out.ok:
            q = urlencode({"generated": "1"})
            return RedirectResponse(url=f"{request.url.path}?{q}", status_code=303)
        chart_notice = out.message
    else:
        ensure_trade_chart_if_possible(root, tid, force=False)

    png_path = trade_review_chart_png_path(root, tid)
    raw_payload = find_paper_order_payload_by_trade_id(root, tid) or rec.raw_json
    has_chart = png_path.is_file()
    has_local_day_cache = candles_available_for_trade(root, raw_payload) if raw_payload else False
    risk_ps, reward_ps = _risk_reward_per_share(rec)

    sizing_blob = ""
    if raw_payload.get("sizing_audit") or raw_payload.get("sizing_summary"):
        sizing_blob = json.dumps(
            {
                "sizing_audit": raw_payload.get("sizing_audit"),
                "sizing_summary": raw_payload.get("sizing_summary"),
            },
            indent=2,
            ensure_ascii=False,
        )

    ctx = base_context(request, active="trades")
    ctx.update(
        {
            "tr": rec,
            "trade_status_key": _status_key(rec.status_slug),
            "close_reason_i18n": _close_reason_key(rec.close_reason),
            "skipped_human": skipped_reason_human_for(rec, locale=loc),
            "has_chart": has_chart,
            "chart_notice": chart_notice,
            "generated_flag": bool(generated),
            "has_local_day_cache": has_local_day_cache,
            "raw_audit_json": json.dumps(raw_payload, indent=2, sort_keys=False, ensure_ascii=False),
            "trade_chart_png_url": request.url_for("trades_trade_chart_png", trade_id=tid),
            "risk_per_share": risk_ps,
            "reward_per_share": reward_ps,
            "raw_json_truncated_hints": sizing_blob or "",
        }
    )
    return request.app.state.templates.TemplateResponse(request, "trade_detail.html", ctx)


@router.get("/trades/{trade_id}/chart.png", name="trades_trade_chart_png")
def trades_trade_chart_png(request: Request, trade_id: str) -> Response:
    root: Path = request.app.state.project_root
    tid = (trade_id or "").strip().lower()
    if find_trade_record(root, tid) is None:
        return Response(status_code=404)
    p = trade_review_chart_png_path(root, tid)
    if not p.is_file():
        return Response(status_code=404)
    return FileResponse(p, media_type="image/png")
