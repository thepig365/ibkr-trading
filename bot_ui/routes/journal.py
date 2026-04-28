"""Journal page (Prompt 13F PART E) + trade review (read-only, no broker imports)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from bot.journal_trade_charts_pipeline import (
    ensure_trade_chart_if_possible,
    journal_chart_cell,
    journal_page_auto_ensure_row_charts,
)
from bot.journal_trade_lookup import find_paper_order_payload_by_trade_id
from bot.trade_journal_chart import (
    generate_trade_journal_chart_png,
    trade_review_chart_png_path,
)
from bot.ux.humanize import humanize_skip_reason

from ..i18n import get_locale
from ._helpers import base_context

router = APIRouter()


def _float_or_none(val: object) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _journal_trade_status_i18n_key(row: object) -> str:
    sr = getattr(row, "skipped_reasons", None) or []
    sk = len(sr) > 0
    sub = bool(getattr(row, "submitted", False))
    sb = bool(getattr(row, "submitted_to_broker", False))
    bi = str(getattr(row, "bracket_integrity", "") or "").strip().lower()
    if sk and not sub and not sb:
        return "journal.status_skipped"
    if sb and not sub:
        return "journal.status_partial"
    if bi == "incomplete" and (sub or sb):
        return "journal.status_protection_incomplete"
    if sub or sb:
        return "journal.status_sent"
    if sk:
        return "journal.status_skipped"
    return "journal.status_unknown"


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
    loc = get_locale(request)
    journal_page_auto_ensure_row_charts(root, journal.paper_orders)

    ctx.update(
        {
            "journal": journal,
            "page_title": "Trade Journal (paper + backtest)",
            "journal_filter": view_filter,
            "journal_symbol": symbol.strip().upper(),
            "journal_direction": (direction or "all").strip().lower(),
            "journal_chart_filter": (chart_filter or "all").strip().lower(),
            "journal_session_scope": (session_scope or "all").strip().lower(),
            "journal_chart_cell": lambda r: journal_chart_cell(root, r),
            "humanize_skip": lambda s: humanize_skip_reason(s, locale=loc),
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
            request,
            "journal_trade_not_found.html",
            ctx,
            status_code=404,
        )
    tid = row.trade_id
    root: Path = request.app.state.project_root
    loc = get_locale(request)
    chart_notice: str | None = None
    if preview_chart == 1:
        out = generate_trade_journal_chart_png(root, tid, force=True, locale=loc)
        if out.ok:
            q = urlencode({"generated": "1"})
            return RedirectResponse(
                url=f"{request.url.path}?{q}",
                status_code=303,
            )
        chart_notice = out.message
    else:
        ensure_trade_chart_if_possible(root, tid, force=False)

    png_path = trade_review_chart_png_path(root, tid)
    has_chart = png_path.is_file()
    raw_payload = find_paper_order_payload_by_trade_id(root, tid) or {}
    from bot.trade_journal_chart import candles_available_for_trade  # noqa: PLC0415

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

    skipped_h = [
        humanize_skip_reason(s, locale=loc)
        for s in (list(row.skipped_reasons or []))
    ]
    r_multiple = row.planned_rr
    multiplier_label = ""
    if r_multiple is not None:
        multiplier_label = f"{float(r_multiple):g}R"

    ctx = base_context(request, active="journal")
    ctx.update(
        {
            "jr": row,
            "jr_timestamp": (row.timestamp or "")[:26],
            "trade_status_key": _journal_trade_status_i18n_key(row),
            "risk_multiple_label": multiplier_label,
            "risk_per_share": risk_ps,
            "reward_per_share": reward_ps,
            "skipped_human": skipped_h,
            "skipped_raw_lines": list(row.skipped_reasons or []),
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
