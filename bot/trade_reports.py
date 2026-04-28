"""Trader-facing journal analytics from local ``TradeLedgerRecord`` rows only (no IBKR).

Dollar P/L is only plotted when per-trade realized USD can be read honestly from JSON;
otherwise R-based metrics are preferred.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from .trade_ledger import TradeLedgerRecord, build_trade_records, ledger_summary_counts

_NY = ZoneInfo("America/New_York")


def _parse_ts_day(ts: str | None) -> date | None:
    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(_NY).date()
    except ValueError:
        return None


def _hour_bucket_ny(ts: str | None) -> int | None:
    """Hour-of-day 0–23 in **America/New_York**, from any ISO-ish timestamp."""

    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return int(dt.astimezone(_NY).hour)
    except ValueError:
        return None


def realized_pnl_usd_from_raw(obj: dict[str, Any]) -> float | None:
    """Return USD realized P/L if clearly present; never guess."""

    for key in (
        "realized_pnl_usd",
        "realized_dollar_pnl",
        "pnl_usd",
        "realized_pnl",
        "closed_pnl_usd",
    ):
        v = obj.get(key)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if abs(x) < 1e12:
            return x
    return None


def _closed_with_r(rec: TradeLedgerRecord) -> bool:
    return rec.status_slug == "closed" and rec.realized_r is not None


def _closed_with_pnl(rec: TradeLedgerRecord) -> tuple[bool, float | None]:
    ok = rec.status_slug == "closed" and rec.exit_time and rec.exit_price is not None
    if not ok:
        return False, None
    p = realized_pnl_usd_from_raw(rec.raw_json)
    return p is not None, p


def all_closed_trades_have_reliable_usd(records: list[TradeLedgerRecord]) -> bool:
    """True only if every *closed* row has explicit realized USD in JSON (same rule as P/L curve)."""

    closed = [r for r in records if r.status_slug == "closed"]
    if not closed:
        return False
    for r in closed:
        ok, _p = _closed_with_pnl(r)
        if not ok:
            return False
    return True


def _svg_bars_counts(hour_to_n: dict[int, int], *, width: int = 440, height: int = 105, fill: str = "#94a3b8") -> str:
    """Simple bar chart for non-negative integer counts by hour key."""

    if not hour_to_n:
        return ""
    keys = sorted(hour_to_n.keys())
    vals = [float(hour_to_n[k]) for k in keys]
    mv = max(vals) if vals else 1.0
    if mv < 1e-12:
        mv = 1.0
    bw = 380 / max(1, len(vals))
    rects = []
    for i, v in enumerate(vals):
        h = (v / mv) * 85
        x = 30 + i * bw
        rects.append(
            f'<rect x="{x:.1f}" y="{95 - h:.1f}" width="{bw * 0.8:.1f}" height="{h:.1f}" fill="{fill}" />'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
        + "".join(rects)
        + "</svg>"
    )


@dataclass
class JournalAnalytics:
    """Serializable analytics payload for UI / JSON."""

    empty_state: bool = False
    empty_message_key: str = "reports.not_enough_closed_trades"
    total_trades: int = 0
    closed_trades: int = 0
    open_trades: int = 0
    skipped_trades: int = 0
    pending_trades: int = 0
    win_rate_closed: float | None = None
    avg_r_closed: float | None = None
    total_r_closed: float | None = None
    expectancy_r: float | None = None
    profit_factor_r: float | None = None
    max_drawdown_r: float | None = None
    has_reliable_pnl_usd: bool = False
    cumulative_r_points: list[tuple[str, float]] = field(default_factory=list)
    cumulative_pnl_points: list[tuple[str, float]] = field(default_factory=list)
    drawdown_r_points: list[tuple[str, float]] = field(default_factory=list)
    daily_r: dict[str, float] = field(default_factory=dict)
    daily_pnl: dict[str, float] = field(default_factory=dict)
    r_histogram: dict[str, int] = field(default_factory=dict)
    performance_by_symbol: dict[str, dict[str, float]] = field(default_factory=dict)
    performance_by_exit_hour: dict[str, dict[str, float]] = field(default_factory=dict)
    decisions_by_submitted_hour: dict[str, int] = field(default_factory=dict)
    skipped_reason_counts: dict[str, int] = field(default_factory=dict)
    cumulative_r_svg: str = ""
    cumulative_pnl_svg: str = ""
    drawdown_r_svg: str = ""
    daily_r_svg: str = ""
    r_distribution_svg: str = ""
    perf_symbol_svg: str = ""
    perf_exit_hour_svg: str = ""
    decisions_submitted_hour_svg: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _histogram_bins(rs: list[float], bins: int = 12) -> dict[str, int]:
    if not rs:
        return {}
    mn = min(rs)
    mx = max(rs)
    if abs(mx - mn) < 1e-12:
        return {"single": len(rs)}
    step = (mx - mn) / float(bins)
    out: dict[str, int] = {}
    for rv in rs:
        i = min(bins - 1, int((rv - mn) / step) if step > 0 else 0)
        lo = mn + i * step
        hi = mn + (i + 1) * step
        key = f"{lo:.2f}–{hi:.2f}"
        out[key] = out.get(key, 0) + 1
    return out


def svg_line_series(
    points: list[tuple[float, float]],
    *,
    width: int = 440,
    height: int = 140,
    stroke: str = "#3b82f6",
    fill: str = "none",
    baseline: bool = True,
) -> str:
    """Minimal SVG polyline; *points* are (xratio 0..1, yratio 0..1) with y up = good."""

    if len(points) < 2:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="chart">'
            f'<text x="12" y="{height // 2}" fill="#888" font-size="12">—</text></svg>'
        )
    pad = 12
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    cmds: list[str] = []
    for i, (xr, yr) in enumerate(points):
        x = pad + xr * inner_w
        y = pad + (1.0 - yr) * inner_h
        cmds.append(f"{x:.1f},{y:.1f}")
    d = " ".join(cmds)
    base = ""
    if baseline:
        base = f'<line x1="{pad}" y1="{pad + inner_h}" x2="{width - pad}" y2="{pad + inner_h}" stroke="#444" stroke-width="1"/>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
        f'{base}'
        f'<polyline fill="{fill}" stroke="{stroke}" stroke-width="2" points="{d}" />'
        f"</svg>"
    )


def _normalize_y(vals: list[float]) -> list[float]:
    if not vals:
        return []
    lo = min(vals)
    hi = max(vals)
    if abs(hi - lo) < 1e-12:
        return [0.5 for _ in vals]
    return [(v - lo) / (hi - lo) for v in vals]


def _normalize_y_centered(vals: list[float]) -> list[float]:
    """For drawdown (negative); map min..max to 0..1."""

    return _normalize_y(vals)


def build_journal_analytics(records: list[TradeLedgerRecord]) -> JournalAnalytics:
    out = JournalAnalytics(total_trades=len(records))

    closed_r: list[tuple[str, float, TradeLedgerRecord]] = []
    for rec in records:
        if rec.status_slug == "open":
            out.open_trades += 1
        elif rec.status_slug == "skipped":
            out.skipped_trades += 1
            rk = (rec.skipped_reason_raw or "unknown").strip() or "unknown"
            out.skipped_reason_counts[rk] = out.skipped_reason_counts.get(rk, 0) + 1
            sh = _hour_bucket_ny(rec.submitted_time)
            if sh is not None:
                sk = str(sh)
                out.decisions_by_submitted_hour[sk] = out.decisions_by_submitted_hour.get(sk, 0) + 1
        elif rec.status_slug == "pending":
            out.pending_trades += 1
        elif rec.status_slug == "closed" and _closed_with_r(rec):
            ts_k = (rec.exit_time or rec.submitted_time or "")[:32]
            closed_r.append((ts_k, float(rec.realized_r or 0.0), rec))

    closed_r.sort(key=lambda x: x[0])
    out.closed_trades = len(closed_r)

    if out.closed_trades == 0:
        out.empty_state = True
        if out.decisions_by_submitted_hour:
            out.decisions_submitted_hour_svg = _svg_bars_counts(
                {int(k): int(v) for k, v in out.decisions_by_submitted_hour.items()}
            )
        return out

    rs = [r for _, r, _ in closed_r]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    out.win_rate_closed = len(wins) / len(rs) if rs else None
    out.avg_r_closed = sum(rs) / len(rs)
    out.total_r_closed = sum(rs)
    out.expectancy_r = out.avg_r_closed

    if gross_loss > 1e-12 and gross_win > 0:
        out.profit_factor_r = gross_win / gross_loss
    elif gross_loss < 1e-12 and gross_win > 0:
        out.profit_factor_r = None

    cum: list[float] = []
    s = 0.0
    peak = 0.0
    dd_pts: list[float] = []
    cum_pts: list[tuple[str, float]] = []
    dd_series: list[tuple[str, float]] = []
    for ts_k, rv, _rec in closed_r:
        s += rv
        cum.append(s)
        peak = max(peak, s)
        dd = peak - s
        dd_pts.append(dd)
        cum_pts.append((ts_k[:10], s))
        dd_series.append((ts_k[:10], -dd))

    out.cumulative_r_points = [(a, b) for a, b in cum_pts]

    if cum:
        if len(cum) == 1:
            xr = [0.0, 1.0]
            yn_c = [0.5, 0.5]
            ydd = [0.0, 0.0]
        else:
            xr = [i / float(len(cum) - 1) for i in range(len(cum))]
            yn_c = _normalize_y(cum)
            ydd = _normalize_y_centered(dd_pts)
        out.drawdown_r_points = dd_series
        out.cumulative_r_svg = svg_line_series(list(zip(xr, yn_c)), stroke="#22c55e")
        out.drawdown_r_svg = svg_line_series(list(zip(xr, ydd)), stroke="#f97316")

    # max drawdown R (from running peak)
    max_dd = max(dd_pts) if dd_pts else 0.0
    out.max_drawdown_r = float(max_dd)

    # daily R
    daily: dict[str, list[float]] = {}
    for ts_k, rv, rec in closed_r:
        dkey = _parse_ts_day(rec.exit_time or rec.submitted_time)
        if dkey is None:
            continue
        ds = dkey.isoformat()
        daily.setdefault(ds, []).append(rv)
    out.daily_r = {k: sum(v) for k, v in sorted(daily.items())}

    if len(out.daily_r) >= 2:
        ks = sorted(out.daily_r.keys())
        vals = [out.daily_r[k] for k in ks]
        xr = [i / max(1, len(vals) - 1) for i in range(len(vals))]
        yn = _normalize_y(vals)
        out.daily_r_svg = svg_line_series(list(zip(xr, yn)), stroke="#6366f1")

    out.r_histogram = _histogram_bins(rs)

    sym_acc: dict[str, list[float]] = {}
    for ts_k, rv, rec in closed_r:  # noqa: B007
        sy = (rec.symbol or "").upper()
        if not sy:
            continue
        sym_acc.setdefault(sy, []).append(rv)
    for sy, lst in sym_acc.items():
        out.performance_by_symbol[sy] = {
            "count": float(len(lst)),
            "total_r": float(sum(lst)),
            "avg_r": float(sum(lst) / len(lst)),
        }

    # R performance: bucket by **exit** time (NY hour). Fallback to submitted only if exit missing (should not happen for closed_r).
    hr_exit: dict[int, list[float]] = {}
    for ts_k, rv, rec in closed_r:
        hb = _hour_bucket_ny(rec.exit_time or rec.submitted_time)
        if hb is None:
            continue
        hr_exit.setdefault(hb, []).append(rv)
    for h in sorted(hr_exit.keys()):
        lst = hr_exit[h]
        out.performance_by_exit_hour[str(h)] = {
            "count": float(len(lst)),
            "total_r": float(sum(lst)),
            "avg_r": float(sum(lst) / len(lst)),
        }

    # histogram SVG (bar positions)
    hist_items = sorted(out.r_histogram.items(), key=lambda x: -x[1])[:14]
    if hist_items:
        mv = max(v for _, v in hist_items)
        bw = 420 / max(1, len(hist_items))
        bars = []
        for i, (_label, v) in enumerate(hist_items):
            h = (v / mv) * 100 if mv else 0
            x = 10 + i * bw
            bars.append(
                f'<rect x="{x:.1f}" y="{110 - h:.1f}" width="{bw * 0.85:.1f}" height="{h:.1f}" fill="#64748b"/>'
            )
        out.r_distribution_svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="440" height="120" viewBox="0 0 440 120" role="img">'
            + "".join(bars)
            + "</svg>"
        )

    perf_syms = sorted(out.performance_by_symbol.items(), key=lambda x: x[1]["total_r"], reverse=True)[:12]
    if perf_syms:
        mv = max(abs(x[1]["total_r"]) for x in perf_syms) or 1.0
        bw = 400 / max(1, len(perf_syms))
        rects = []
        for i, (_sy, data) in enumerate(perf_syms):
            tr = data["total_r"]
            h = abs(tr) / mv * 90
            x = 20 + i * bw
            color = "#22c55e" if tr >= 0 else "#ef4444"
            rects.append(
                f'<rect x="{x:.1f}" y="{100 - h:.1f}" width="{bw * 0.82:.1f}" height="{h:.1f}" fill="{color}" />'
            )
        out.perf_symbol_svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="440" height="110" viewBox="0 0 440 110">'
            + "".join(rects)
            + "</svg>"
        )

    ex_order = sorted(out.performance_by_exit_hour.keys(), key=lambda x: int(x))
    if len(ex_order) >= 1:
        vals = [out.performance_by_exit_hour[h]["total_r"] for h in ex_order]
        mv = max(abs(min(vals)), abs(max(vals)), 1e-9)
        bw = 380 / max(1, len(vals))
        rects = []
        for i, tr in enumerate(vals):
            h = abs(tr) / mv * 85
            x = 30 + i * bw
            color = "#38bdf8"
            rects.append(
                f'<rect x="{x:.1f}" y="{95 - h:.1f}" width="{bw * 0.8:.1f}" height="{h:.1f}" fill="{color}" />'
            )
        out.perf_exit_hour_svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="440" height="105" viewBox="0 0 440 105">'
            + "".join(rects)
            + "</svg>"
        )

    if out.decisions_by_submitted_hour:
        out.decisions_submitted_hour_svg = _svg_bars_counts(
            {int(k): int(v) for k, v in out.decisions_by_submitted_hour.items()}
        )

    # P/L USD curve (only when every closed trade has numeric P/L)
    pnls: list[tuple[str, float]] = []
    reliable = True
    cum_p = 0.0
    for ts_k, rv, rec in closed_r:
        ok, pu = _closed_with_pnl(rec)
        if not ok:
            reliable = False
            break
        assert pu is not None
        cum_p += pu
        pnls.append((rec.submitted_time[:10] if rec.submitted_time else ts_k[:10], cum_p))

    if reliable and pnls and len(pnls) == len(closed_r):
        out.has_reliable_pnl_usd = True
        out.cumulative_pnl_points = pnls
        vals = [p for _, p in pnls]
        xr = [i / max(1, len(vals) - 1) for i in range(len(vals))]
        yn = _normalize_y(vals)
        out.cumulative_pnl_svg = svg_line_series(list(zip(xr, yn)), stroke="#eab308")
    else:
        out.has_reliable_pnl_usd = False

    return out


def build_journal_analytics_for_project(project_root: Path | str) -> JournalAnalytics:
    root = Path(project_root).resolve()
    rows = build_trade_records(root)
    return build_journal_analytics(rows)


def build_data_quality_payload(
    records: list[TradeLedgerRecord], ledger_counts: dict[str, Any]
) -> dict[str, Any]:
    """Counts for the dashboard Data Quality card (honest, file-only)."""

    total = len(records)
    closed_with_exit = sum(1 for r in records if r.status_slug == "closed")
    missing_exit = sum(
        1 for r in records if r.status_slug in ("open", "pending", "protection_incomplete")
    )
    reliable_usd: bool | None
    if closed_with_exit == 0:
        reliable_usd = None
    else:
        reliable_usd = all_closed_trades_have_reliable_usd(records)

    return {
        "no_trade_records": total == 0,
        "closed_with_exit": closed_with_exit,
        "missing_exit": missing_exit,
        "charts_available": int(ledger_counts.get("charts_available", 0)),
        "charts_missing_candles": int(ledger_counts.get("charts_missing_candles", 0)),
        "reliable_usd_pnl": reliable_usd,
    }


def _fmt_price(v: float | None) -> str:
    """Short display string for entry/exit; empty when unknown."""

    if v is None:
        return ""
    try:
        f = float(v)
        s = f"{f:.6f}".rstrip("0").rstrip(".")
        return s if s else "—"
    except (TypeError, ValueError):
        return ""


@dataclass
class DashboardTradeContext:
    """Compact trader-facing dashboard snapshot."""

    today_r_ny: float | None
    today_closed_realized_count: int
    cumulative_r_total: float | None
    open_pipeline_total: int
    explain_open_no_closed: bool
    explain_missing_charts_block: bool
    performance_pending: bool
    latest_trades: list[dict[str, Any]]
    ledger_counts: dict[str, Any]
    data_quality: dict[str, Any]
    action_required: list[str]
    mini_r_svg: str
    ny_today: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_dashboard_trade_context(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    rows = build_trade_records(root)
    counts = ledger_summary_counts(rows, root)
    today_ny = datetime.now(_NY).date().isoformat()

    today_r = 0.0
    today_closed_realized_count = 0
    for rec in rows:
        if rec.status_slug != "closed" or rec.realized_r is None:
            continue
        dexit = _parse_ts_day(rec.exit_time or rec.submitted_time)
        if not dexit or dexit.isoformat() != today_ny:
            continue
        today_closed_realized_count += 1
        today_r += float(rec.realized_r)

    closed_series: list[float] = []
    for rec in sorted(
        rows,
        key=lambda r: (r.submitted_time or "", r.exit_time or ""),
    ):
        if rec.status_slug == "closed" and rec.realized_r is not None:
            closed_series.append(float(rec.realized_r))
    cumulative: list[float] = []
    s = 0.0
    for rv in closed_series:
        s += rv
        cumulative.append(s)

    performance_pending = len(closed_series) < 2
    mini = ""
    if len(cumulative) >= 2:
        xr = [i / float(len(cumulative) - 1) for i in range(len(cumulative))]
        yn = _normalize_y(cumulative)
        mini = svg_line_series(list(zip(xr, yn)), width=320, height=90, stroke="#22c55e")

    latest: list[dict[str, Any]] = []
    for rec in rows[:5]:
        has_png = bool(rec.chart_path)
        latest.append(
            {
                "trade_id": rec.trade_id,
                "symbol": rec.symbol,
                "direction": (rec.direction or "—"),
                "status_slug": rec.status_slug,
                "submitted_time": rec.submitted_time[:19] if rec.submitted_time else "",
                "entry_price": rec.entry_price,
                "exit_price": rec.exit_price,
                "entry_display": _fmt_price(rec.entry_price),
                "exit_display": _fmt_price(rec.exit_price),
                "realized_r": rec.realized_r,
                "chart_status": rec.chart_status or "",
                "has_chart_png": has_png,
            }
        )

    ope = int(counts.get("open", 0)) + int(counts.get("pending", 0)) + int(
        counts.get("protection_incomplete", 0)
    )
    closed_n = int(counts.get("closed", 0))
    explain_open_no_closed = ope > 0 and closed_n == 0
    explain_mc = int(counts.get("charts_missing_candles", 0)) > 0

    dq_payload = build_data_quality_payload(rows, counts)

    actions: list[str] = []
    inc = counts.get("protection_incomplete", 0)
    miss = counts.get("charts_missing_candles", 0)
    if dq_payload.get("missing_exit", 0) > 0:
        actions.append("missing_exit_records")
    if inc:
        actions.append("protection_incomplete")
    if miss:
        actions.append("charts_missing_candles")

    ctx = DashboardTradeContext(
        today_r_ny=(today_r if today_closed_realized_count else None),
        today_closed_realized_count=today_closed_realized_count,
        cumulative_r_total=float(s) if closed_series else None,
        open_pipeline_total=ope,
        explain_open_no_closed=explain_open_no_closed,
        explain_missing_charts_block=explain_mc,
        performance_pending=performance_pending,
        latest_trades=latest,
        ledger_counts=counts,
        data_quality=dq_payload,
        action_required=actions,
        mini_r_svg=mini,
        ny_today=today_ny,
    )
    return ctx.to_dict()


__all__ = [
    "JournalAnalytics",
    "all_closed_trades_have_reliable_usd",
    "build_data_quality_payload",
    "build_journal_analytics",
    "build_journal_analytics_for_project",
    "build_dashboard_trade_context",
    "realized_pnl_usd_from_raw",
]
