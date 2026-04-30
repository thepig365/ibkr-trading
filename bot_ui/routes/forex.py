"""Forex paper orders ledger (JSONL) + optional local candle chart preview."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from bot.config import load_config
from bot.forex.forex_preview_chart import render_forex_bracket_chart_png_bytes
from bot.forex.orders_ui import (
    find_forex_trade_rows,
    iter_forex_order_events,
    summarize_row_for_ui,
)
from bot.forex.ui_auto import build_forex_auto_paper_dashboard
from bot_ui.i18n import append_lang_to_path, get_locale

from ._helpers import base_context

router = APIRouter()


def _sanitize_tid(tid_raw: str) -> str | None:
    s = "".join(c for c in tid_raw.strip().lower() if c in "abcdef0123456789")
    return s if len(s) >= 16 else None


def _recent_rows(project_root: Any, *, locale: str, limit: int = 120) -> list[dict[str, Any]]:
    raw = iter_forex_order_events(project_root, max_files=21)
    tail = raw[-limit:] if len(raw) > limit else raw
    enriched = [summarize_row_ui(r, locale=locale) for r in tail]
    return list(reversed(enriched))


def summarize_row_ui(rec: dict[str, Any], *, locale: str) -> dict[str, Any]:
    s = summarize_row_for_ui(rec)
    chart_q: dict[str, str] = {}
    slug = s.get("pair_slug")
    if slug:
        chart_q["pair_slug"] = str(slug)
    for fld, key in (("entry", "entry"), ("stop", "stop"), ("target", "target")):
        v = rec.get(key) if key in rec else (rec.get("broker") or {}).get(key)
        if v is not None:
            try:
                chart_q[key] = f"{float(v):g}"
            except (TypeError, ValueError):
                pass
    s["chart_query"] = chart_q
    if slug:
        q = {k: v for k, v in chart_q.items() if v}
        path = "/forex/chart.png"
        if q:
            path = f"{path}?{urlencode(q)}"
        s["chart_png_href"] = append_lang_to_path(path, locale)
    else:
        s["chart_png_href"] = None
    tid = s.get("trade_id")
    tc = ""
    if isinstance(tid, str) and _sanitize_tid(tid):
        tc = append_lang_to_path(f"/forex/trade-chart-saved?trade_id={tid}", locale)
    s["saved_chart_png_href"] = tc
    tdet = ""
    if isinstance(tid, str) and tid:
        tdet = append_lang_to_path(f"/forex/trade/{tid}", locale)
    s["trade_detail_href"] = tdet
    return s


@router.get("/forex/chart.png", name="forex_chart_preview")
def forex_chart_preview(
    request: Request,
    pair_slug: str = Query(..., min_length=3, max_length=16),
    entry: float | None = Query(None),
    stop: float | None = Query(None),
    target: float | None = Query(None),
) -> Response:
    root = request.app.state.project_root
    png, err = render_forex_bracket_chart_png_bytes(
        root,
        pair_slug=pair_slug.strip().upper(),
        entry=entry,
        stop=stop,
        target=target,
    )
    if not png:
        msg = (err or "chart_error").encode("utf-8")
        return Response(content=msg, media_type="text/plain", status_code=404)
    return Response(content=png, media_type="image/png")


@router.get("/forex/trade-chart-saved", name="forex_trade_chart_saved")
def forex_trade_chart_saved(
    request: Request,
    trade_id: str = Query(..., min_length=16, max_length=64),
) -> Response:
    """Serve ``data/reports/forex_trade_charts/{trade_id}.png`` when present."""

    root = Path(request.app.state.project_root).resolve()
    tidsafe = _sanitize_tid(trade_id)
    if not tidsafe:
        raise HTTPException(status_code=404, detail="invalid trade_id")
    p = root / "data/reports/forex_trade_charts" / f"{tidsafe}.png"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="chart not found")
    return FileResponse(path=str(p), media_type="image/png")


@router.get("/forex", response_class=HTMLResponse, name="forex_page")
def forex_page(request: Request) -> HTMLResponse:
    root = request.app.state.project_root
    ctx = base_context(request, active="forex")
    cfg = load_config(project_root=root)
    try:
        ctx["forex_auto_ui"] = build_forex_auto_paper_dashboard(root, cfg=cfg)
    except (OSError, TypeError, ValueError):
        ctx["forex_auto_ui"] = {}
    loc = get_locale(request)
    ctx["forex_order_rows"] = _recent_rows(root, locale=loc)
    ctx["recent_command_results"] = request.app.state.command_queue.list_recent(limit=8)

    ctx["forex_disclaimer_en"] = (
        "Paper only. Uses IBKR TWS paper account. Daily notional cap USD 100,000. "
        "No market orders."
    )
    ctx["forex_disclaimer_zh"] = (
        "仅纸面账户。使用 IBKR TWS paper。每日名义上限 USD 100,000。禁止市价单。"
    )
    return request.app.state.templates.TemplateResponse(request, "forex.html", ctx)


@router.get(
    "/forex/trade/{trade_id}",
    response_class=HTMLResponse,
    name="forex_trade_detail",
)
def forex_trade_detail(request: Request, trade_id: str) -> HTMLResponse:
    """Single-trade detail from JSONL (+ saved chart if present)."""

    root = request.app.state.project_root
    rp = Path(root)
    loc = get_locale(request)
    rows_raw = find_forex_trade_rows(rp, trade_id)
    summarized = summarize_row_ui(rows_raw[-1], locale=loc) if rows_raw else {}
    tidsafe = _sanitize_tid(trade_id) or ""
    png_exists = (rp / "data/reports/forex_trade_charts" / f"{tidsafe}.png").is_file()
    chart_href = None
    if png_exists and tidsafe:
        chart_href = append_lang_to_path(
            f"/forex/trade-chart-saved?trade_id={trade_id}", loc
        )

    ctx = base_context(request, active="forex")
    ctx["trade_id"] = trade_id
    ctx["forex_trade_rows_raw"] = rows_raw
    ctx["forex_trade_summary"] = summarized
    ctx["forex_trade_chart_href"] = chart_href
    ctx["recent_command_results"] = request.app.state.command_queue.list_recent(limit=6)
    return request.app.state.templates.TemplateResponse(request, "forex_trade.html", ctx)


__all__ = ["router"]
