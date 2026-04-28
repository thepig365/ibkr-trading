"""Journal page (Prompt 13F PART E) + trade review (read-only, no broker imports)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from bot.journal_trade_lookup import find_paper_order_payload_by_trade_id
from bot.trade_journal_chart import (
    candles_available_for_trade,
    generate_trade_journal_chart_png,
    trade_review_chart_png_path,
)
from bot.ux.humanize import humanize_skip_reason

from ._helpers import base_context

router = APIRouter()


def _float_or_none(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _trade_status_label(row: object) -> str:
    """Coarse UI status for Identity / review card."""
    skipped = bool(getattr(row, "skipped_reasons", None))
    sub = bool(getattr(row, "submitted", False))
    sub_b = bool(getattr(row, "submitted_to_broker", False))
    bi = str(getattr(row, "bracket_integrity", "") or "").strip().lower()
    if skipped and not sub and not sub_b:
        return "Skipped"
    if sub or sub_b:
        if bi == "complete":
            return "Protected"
        if bi == "incomplete":
            return "Incomplete"
        return "Sent"
    return "—"


@router.get("/journal", response_class=HTMLResponse, name="journal_page")
def journal_page(
    request: Request,
    view_filter: str = Query(
        "all",
        alias="filter",
        description="all|submitted|skipped|incomplete",
    ),
    symbol: str = Query(""),
    direction: str = Query("all", description="all|long|short"),
    chart_filter: str = Query("all", alias="chart", description="all|yes|no"),
    session_scope: str = Query(
        "all",
        alias="session",
        description="all|today|last_session",
    ),
) -> HTMLResponse:
    state = request.app.state.state_store
    journal = state.get_journal_view(
        limit=200,
        view_filter=view_filter,
        symbol=symbol,
        direction=direction,
        chart_filter=chart_filter,
        session_scope=session_scope,
    )
    ctx = base_context(request, active="journal")
    root: Path = request.app.state.project_root

    def _chart_exists(tid: str) -> bool:
        return trade_review_chart_png_path(root, tid).is_file()

    ctx.update(
        {
            "journal": journal,
            "page_title": "Trade Journal (paper + backtest)",
            "journal_filter": view_filter,
            "journal_symbol": symbol.strip().upper(),
            "journal_direction": (direction or "all").strip().lower(),
            "journal_chart_filter": (chart_filter or "all").strip().lower(),
            "journal_session_scope": (session_scope or "all").strip().lower(),
            "chart_png_exists": _chart_exists,
            "humanize_skip": humanize_skip_reason,
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "journal.html", ctx
    )


@router.get(
    "/journal/trade/{trade_id}",
    response_class=HTMLResponse,
    name="journal_trade_review",
)
def journal_trade_review(
    request: Request,
    trade_id: str,
    preview_chart: int = Query(
        0,
        alias="preview_chart",
        ge=0,
        le=1,
        description="1 to build PNG from local candles (no IBKR).",
    ),
    generated: int = Query(0, alias="generated", ge=0, le=1),
) -> HTMLResponse:
    state = request.app.state.state_store
    row = state.lookup_paper_order_by_trade_id(trade_id)
    if row is None:
        ctx = base_context(request, active="journal")
        ctx["unknown_trade_id"] = trade_id
        return request.app.state.templates.TemplateResponse(
            request, "journal_trade_not_found.html",
            ctx,
            status_code=404,
        )
    tid = row.trade_id
    root: Path = request.app.state.project_root
    chart_notice: str | None = None
    if preview_chart == 1:
        out = generate_trade_journal_chart_png(root, tid)
        if out.ok:
            q = urlencode({"generated": "1"})
            return RedirectResponse(
                url=f"{request.url.path}?{q}",
                status_code=303,
            )
        chart_notice = out.message

    png_path = trade_review_chart_png_path(root, tid)
    has_chart = png_path.is_file()
    raw_payload = find_paper_order_payload_by_trade_id(root, tid) or {}
    has_local_day_cache = (
        candles_available_for_trade(root, raw_payload) if raw_payload else False
    )

    entry = _float_or_none(row.entry)
    stop = _float_or_none(row.stop)
    target = _float_or_none(row.target)
    risk_ps: float | None = None
    reward_ps: float | None = None
    dire = (row.direction or "").strip().lower()
    if entry is not None and stop is not None:
        if dire == "short":
            risk_ps = stop - entry
        else:
            risk_ps = entry - stop
    if entry is not None and target is not None:
        if dire == "short":
            reward_ps = entry - target
        else:
            reward_ps = target - entry

    skipped_h = [humanize_skip_reason(s) for s in (row.skipped_reasons or [])]

    ctx = base_context(request, active="journal")
    ctx.update(
        {
            "jr": row,
            "trade_status": _trade_status_label(row),
            "risk_per_share": risk_ps,
            "reward_per_share": reward_ps,
            "skipped_human": skipped_h,
            "has_chart": has_chart,
            "chart_notice": chart_notice,
            "generated_flag": bool(generated),
            "chart_png_url": request.url_for(
                "journal_trade_chart_png", trade_id=tid
            ),
            "has_local_day_cache": has_local_day_cache,
        }
    )
    return request.app.state.templates.TemplateResponse(
        request, "journal_trade_review.html", ctx
    )


@router.get(
    "/journal/trade/{trade_id}/chart.png",
    name="journal_trade_chart_png",
)
def journal_trade_chart_png(request: Request, trade_id: str) -> FileResponse:
    root: Path = request.app.state.project_root
    p = trade_review_chart_png_path(root, trade_id)
    if not p.is_file():
        from fastapi.responses import Response

        return Response(status_code=404)
    return FileResponse(p, media_type="image/png")
