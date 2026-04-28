"""Render trade review PNG charts from cached 1-minute candles only (no IBKR).

Output: ``data/reports/trade_charts/{trade_id}.png`` (under project root).

This module deliberately imports matplotlib lazily inside the render function so
imports of :mod:`bot.journal_trade_lookup` stay lightweight for tests without MPL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .backtests.candle_cache import BarRow, read_candles_csv
from .journal_trade_lookup import find_paper_order_payload_by_trade_id

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class TradeChartOutcome:
    ok: bool
    message: str
    png_path: Path | None = None


def local_candle_file_for_trade(
    project_root: Path,
    symbol: str,
    trade_timestamp_utc: datetime,
) -> Path:
    """Pick the NY-session calendar day's cache file matching *trade_timestamp_utc*."""
    sym_u = (symbol or "").strip().upper()
    if trade_timestamp_utc.tzinfo is None:
        ts = trade_timestamp_utc.replace(tzinfo=_UTC)
    else:
        ts = trade_timestamp_utc.astimezone(_UTC)
    ny_date = ts.astimezone(_NY).date().isoformat()
    return (
        Path(project_root)
        / "data"
        / "candles"
        / sym_u
        / "1min"
        / f"{ny_date}.csv"
    )


def trade_anchor_utc(obj: dict[str, Any]) -> datetime | None:
    raw = str(obj.get("timestamp") or obj.get("ts") or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def filter_bars_window(
    bars: list[BarRow],
    center: datetime,
    *,
    before_mins: int = 30,
    after_mins: int = 60,
) -> list[BarRow]:
    """Keep bars whose timestamp parses to [center - before, center + after] in UTC."""
    start = center - timedelta(minutes=before_mins)
    end = center + timedelta(minutes=after_mins)
    out: list[BarRow] = []
    for b in bars:
        ts = _parse_bar_ts_to_utc(b.timestamp)
        if ts is None:
            continue
        if start <= ts <= end:
            out.append(b)
    out.sort(key=lambda r: _parse_bar_ts_to_utc(r.timestamp) or datetime.min.replace(tzinfo=_UTC))
    return out


def _parse_bar_ts_to_utc(ts: str) -> datetime | None:
    s = (ts or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def trade_review_chart_png_path(project_root: Path, trade_id: str) -> Path:
    return (
        Path(project_root).resolve()
        / "data"
        / "reports"
        / "trade_charts"
        / f"{(trade_id or '').strip().lower()}.png"
    )


def candles_available_for_trade(project_root: Path, obj: dict[str, Any]) -> bool:
    sym = str(obj.get("symbol") or "").strip().upper()
    if not sym:
        return False
    anchor = trade_anchor_utc(obj)
    if anchor is None:
        return False
    p = local_candle_file_for_trade(project_root, sym, anchor)
    return p.is_file()


def generate_trade_journal_chart_png(
    project_root: Path,
    trade_id: str,
    *,
    before_mins: int = 30,
    after_mins: int = 60,
) -> TradeChartOutcome:
    """Write a PNG preview for one journal trade id."""
    tid = (trade_id or "").strip().lower()
    proj = Path(project_root).resolve()
    obj = find_paper_order_payload_by_trade_id(proj, tid)
    if obj is None:
        return TradeChartOutcome(ok=False, message="Trade row not found in local journal files.")
    sym = str(obj.get("symbol") or "").strip().upper()
    if not sym:
        return TradeChartOutcome(ok=False, message="Missing symbol on journal row.")
    anchor = trade_anchor_utc(obj)
    if anchor is None:
        return TradeChartOutcome(ok=False, message="Missing timestamp on journal row.")
    cand_path = local_candle_file_for_trade(proj, sym, anchor)
    if not cand_path.is_file():
        return TradeChartOutcome(
            ok=False,
            message=(
                "No local 1-minute cache for this session day. Expected file "
                f"{cand_path.relative_to(proj)} "
                "(coverage / fetch candles from Backtest or CLI when you choose to)."
            ),
        )
    bars = read_candles_csv(cand_path)
    filtered = filter_bars_window(bars, anchor, before_mins=before_mins, after_mins=after_mins)
    if len(filtered) < 3:
        return TradeChartOutcome(
            ok=False,
            message=(
                "Not enough candle rows inside the ±window near the trade time — "
                f"parsed {len(filtered)} bars from {cand_path.name}."
            ),
        )

    entry = _f(obj.get("entry"))
    stop = _f(obj.get("stop"))
    target = _f(obj.get("target"))
    qty = obj.get("quantity")
    qty_f = float(qty) if qty is not None else None

    submitted = bool(obj.get("submitted"))
    skipped_raw = obj.get("skipped_reasons") or []
    bracket_ok = (str(obj.get("bracket_integrity") or "").strip().lower() == "complete")

    outfile = trade_review_chart_png_path(proj, tid)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    ok_write = _write_matplotlib_chart(
        filtered,
        outfile,
        anchor=anchor,
        symbol=sym,
        entry=entry,
        stop=stop,
        target=target,
        qty=qty_f,
        submitted=submitted,
        skipped=bool(skipped_raw),
        bracket_complete=bracket_ok,
    )
    if not ok_write.startswith("OK"):
        return TradeChartOutcome(ok=False, message=ok_write)

    try:
        rel = outfile.relative_to(proj)
    except ValueError:
        rel = outfile
    return TradeChartOutcome(
        ok=True,
        message=f"Wrote {rel}",
        png_path=outfile,
    )


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _write_matplotlib_chart(
    bars: list[BarRow],
    outfile: Path,
    *,
    anchor: datetime,
    symbol: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
    qty: float | None,
    submitted: bool,
    skipped: bool,
    bracket_complete: bool,
) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as exc:  # pragma: no cover - env without mpl
        return f"MPL_UNAVAILABLE: {exc}"

    xs: list[datetime] = []
    ys: list[float] = []
    for b in bars:
        tt = _parse_bar_ts_to_utc(b.timestamp)
        if tt is None:
            continue
        xs.append(tt.astimezone(_NY))
        ys.append(float(b.close))

    if len(xs) < 2:
        return "PARSE_EMPTY"

    fig, ax = plt.subplots(figsize=(11, 5), dpi=120)
    ax.plot(xs, ys, color="#3b82f6", linewidth=1.2, label="Close")
    anchor_ny = anchor.astimezone(_NY)
    ax.axvline(anchor_ny, color="#eab308", linewidth=1.0, linestyle="--", alpha=0.9)

    y_min = min(ys)
    y_max = max(ys)
    pad = max((y_max - y_min) * 0.05, 0.05)

    if entry is not None and stop is not None:
        y_lo = min(entry, stop)
        y_hi = max(entry, stop)
        ax.axhspan(y_lo - pad * 0.1, y_hi + pad * 0.1, facecolor="#ef4444", alpha=0.12, label="Risk zone")

    if entry is not None and target is not None:
        y_lo = min(entry, target)
        y_hi = max(entry, target)
        ax.axhspan(y_lo - pad * 0.1, y_hi + pad * 0.1, facecolor="#22c55e", alpha=0.12, label="Reward zone")

    for y, color, lbl in (
        (entry, "#f97316", "Entry"),
        (stop, "#dc2626", "Stop"),
        (target, "#16a34a", "Target"),
    ):
        if y is None:
            continue
        ax.axhline(y=y, color=color, linewidth=1.1, linestyle="-", alpha=0.95)
        ax.text(
            xs[0],
            y,
            f" {lbl} {y:.2f}",
            color=color,
            fontsize=9,
            va="bottom",
            ha="left",
        )

    headline = []
    headline.append(symbol)
    if submitted:
        headline.append("submitted=True")
    else:
        headline.append("submitted=False")
    if skipped:
        headline.append("skipped=True")
    if bracket_complete:
        headline.append("bracket=COMPLETE")
    else:
        headline.append("bracket=INCOMPLETE/WARN")

    qty_s = ""
    if qty is not None:
        qty_s = f" qty={qty:g}"

    plt.title("\n".join([" · ".join(headline) + qty_s, anchor_ny.strftime("%Y-%m-%d %H:%M %Z")]))
    plt.xlabel("NY time")
    plt.ylabel("Price")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()

    plt.grid(True, alpha=0.25)
    ax.legend(loc="upper left")
    try:
        fig.tight_layout()
        fig.savefig(outfile, bbox_inches="tight")
    except OSError as exc:
        plt.close(fig)
        return str(exc)
    plt.close(fig)
    return "OK"
