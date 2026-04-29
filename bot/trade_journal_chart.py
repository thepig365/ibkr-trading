"""Render trade review PNG charts from cached 1-minute candles only (no IBKR).

Output: ``data/reports/trade_charts/{trade_id}.png`` (under project root).

This module deliberately imports matplotlib lazily inside the render function so
imports of :mod:`bot.journal_trade_lookup` stay lightweight for tests without MPL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict
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


class TradeChartCliPayload(TypedDict, total=False):
    status: str
    chart_path: str | None
    trade_id: str
    symbol: str | None
    date: str | None
    detail: str


def trade_chart_cli_payload(
    project_root: Path,
    trade_id: str,
    *,
    force: bool = False,
    before_mins: int = 30,
    after_mins: int = 60,
) -> TradeChartCliPayload:
    """Structured result for ``generate-trade-chart`` / ``journal-generate-trade-chart`` CLI (no IBKR)."""
    tid = (trade_id or "").strip().lower()
    proj = Path(project_root).resolve()
    out: TradeChartCliPayload = {
        "status": "error",
        "chart_path": None,
        "trade_id": tid,
        "symbol": None,
        "date": None,
        "detail": "",
    }
    obj = find_paper_order_payload_by_trade_id(proj, tid)
    if obj is None:
        out["status"] = "trade_not_found"
        out["detail"] = "Trade row not found in local journal files."
        return out
    from bot.fills_reconciliation import merge_reconciliation_into_trade_payload  # noqa: PLC0415

    obj = merge_reconciliation_into_trade_payload(proj, tid, dict(obj))
    sym = str(obj.get("symbol") or "").strip().upper()
    out["symbol"] = sym or None
    ef_raw = str(obj.get("entry_fill_time") or "").strip()
    anchor = iso_timestamp_to_utc(ef_raw) if ef_raw else trade_anchor_utc(obj)
    if anchor is not None:
        out["date"] = anchor.astimezone(_NY).date().isoformat()
    res = generate_trade_journal_chart_png(
        proj,
        tid,
        before_mins=before_mins,
        after_mins=after_mins,
        force=force,
    )
    out["detail"] = res.message
    if res.ok and res.png_path is not None:
        out["chart_path"] = str(res.png_path)
        low = res.message.lower()
        if "already exists" in low:
            out["status"] = "already_exists"
        else:
            out["status"] = "generated"
        return out
    low = res.message.lower()
    if "not found" in low and "trade row" in low:
        out["status"] = "trade_not_found"
    elif "no local" in low or "expected file" in low:
        out["status"] = "missing_candles"
    elif "not enough" in low:
        out["status"] = "missing_candles"
    else:
        out["status"] = "error"
    return out


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
    return iso_timestamp_to_utc(raw)


def iso_timestamp_to_utc(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
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


def filter_bars_trade_window(
    bars: list[BarRow],
    anchor: datetime,
    exit_time_utc: datetime | None,
    *,
    before_mins: int = 30,
    after_mins: int = 60,
    exit_padding_mins: int = 5,
) -> list[BarRow]:
    """Extend the default window past *exit_time_utc* when a full exit is recorded (same CSV day).

    Typical: 30m before submission/anchor, ≥60m after anchor, or through exit (+padding).
    """
    start = anchor - timedelta(minutes=before_mins)
    end = anchor + timedelta(minutes=after_mins)
    if exit_time_utc is not None:
        ex = exit_time_utc.astimezone(_UTC)
        end = max(end, ex + timedelta(minutes=exit_padding_mins))
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


def candles_available_for_journal_row(project_root: Path, row: Any) -> bool:
    """True if NY-day 1m cache file exists for this journal row (read-only probe)."""
    d = {"timestamp": str(getattr(row, "timestamp", "")), "symbol": str(getattr(row, "symbol", ""))}
    return candles_available_for_trade(project_root, d)


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
    force: bool = False,
    locale: str = "en",
) -> TradeChartOutcome:
    """Write a PNG preview for one journal trade id."""
    tid = (trade_id or "").strip().lower()
    proj = Path(project_root).resolve()
    obj = find_paper_order_payload_by_trade_id(proj, tid)
    if obj is None:
        return TradeChartOutcome(ok=False, message="Trade row not found in local journal files.")
    from bot.fills_reconciliation import merge_reconciliation_into_trade_payload  # noqa: PLC0415
    from bot.fills_reconciliation import trade_reconciliation_map  # noqa: PLC0415

    obj = merge_reconciliation_into_trade_payload(proj, tid, dict(obj))

    mmap = trade_reconciliation_map(proj)
    tr_snap = mmap.get(tid)
    rr_trade: float | None = None
    if tr_snap and tr_snap.get("realized_r") is not None:
        try:
            rr_trade = float(tr_snap["realized_r"])
        except (TypeError, ValueError):
            rr_trade = None
    recon_st = str(obj.get("_recon_status") or "").strip()

    sym = str(obj.get("symbol") or "").strip().upper()
    if not sym:
        return TradeChartOutcome(ok=False, message="Missing symbol on journal row.")

    ef_raw = str(obj.get("entry_fill_time") or "").strip()
    anchor = iso_timestamp_to_utc(ef_raw) if ef_raw else None
    if anchor is None:
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
    exit_raw_str = str(obj.get("exit_time") or obj.get("exit_ts") or "").strip()
    exit_dt_utc = iso_timestamp_to_utc(exit_raw_str) if exit_raw_str else None
    exit_px = _f(obj.get("exit_price"))
    planned_exit = exit_dt_utc is not None and exit_px is not None
    if planned_exit:
        filtered = filter_bars_trade_window(
            bars,
            anchor,
            exit_dt_utc,
            before_mins=before_mins,
            after_mins=after_mins,
        )
    else:
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
    has_skip = isinstance(skipped_raw, list) and any(str(s).strip() for s in skipped_raw)
    bracket_ok = (str(obj.get("bracket_integrity") or "").strip().lower() == "complete")
    ict_h = obj.get("higher_timeframe_context_ok")
    ict5 = obj.get("five_min_setup_found")
    ict1 = obj.get("one_min_trigger_found")

    outfile = trade_review_chart_png_path(proj, tid)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    if outfile.is_file() and not force:
        try:
            rel = outfile.relative_to(proj)
        except ValueError:
            rel = outfile
        return TradeChartOutcome(
            ok=True,
            message=f"Chart already exists: {rel} (use force to regenerate)",
            png_path=outfile,
        )
    zh = str(locale).strip().lower().startswith("zh")
    skipped_human_loc = _first_skip_human_line(skipped_raw, locale=locale)
    struct_notes = _structure_notes_from_obj(obj)
    has_recon = bool(recon_st)
    show_exit_missing_note = (
        not has_recon
        and bool(submitted or obj.get("submitted_to_broker"))
        and not has_skip
        and not planned_exit
    )
    ok_write = _write_matplotlib_chart(
        filtered,
        outfile,
        anchor=anchor,
        symbol=sym,
        direction=str(obj.get("direction") or "").strip().lower(),
        planned_rr=_f(obj.get("planned_rr")),
        entry=entry,
        stop=stop,
        target=target,
        qty=qty_f,
        submitted=submitted,
        skipped=has_skip,
        submitted_to_broker=bool(obj.get("submitted_to_broker", False)),
        bracket_complete=bracket_ok,
        signal_category=str(obj.get("signal_category") or ""),
        skipped_human_one=skipped_human_loc,
        locale=locale,
        exit_price=exit_px if planned_exit else None,
        exit_time_utc=exit_dt_utc if planned_exit else None,
        show_exit_missing_note=show_exit_missing_note,
        ict_htf=ict_h,
        ict_5m=ict5,
        ict_1m=ict1,
        zh_locale=zh,
        structure_notes=struct_notes,
        recon_status=recon_st if recon_st else None,
        realized_r_trade=rr_trade,
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


def _first_skip_human_line(skipped_raw: Any, *, locale: str = "en") -> str:
    if not skipped_raw:
        return ""
    sr = skipped_raw if isinstance(skipped_raw, list) else [skipped_raw]
    first = next((str(s).strip() for s in sr if s), "")
    if not first:
        return ""
    from bot.ux.humanize import humanize_skip_reason  # noqa: PLC0415 — chart caption only

    return humanize_skip_reason(first, locale=locale or "en")


def _structure_notes_from_obj(obj: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("bos", "bos_type", "mss", "liquidity_sweep", "fair_value_gap"):
        v = obj.get(key)
        if v is None or str(v).strip() == "":
            continue
        parts.append(f"{key}={v}")
    return " · ".join(parts[:10])


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def trade_chart_annotation_meta(obj: dict[str, Any]) -> dict[str, Any]:
    """Pure helper for tests: which overlays apply (no matplotlib)."""

    skipped = obj.get("skipped_reasons") or []
    sk = isinstance(skipped, list) and any(str(s).strip() for s in skipped)
    ex_raw = str(obj.get("exit_time") or obj.get("exit_ts") or "").strip()
    ex_t_ok = bool(ex_raw and iso_timestamp_to_utc(ex_raw))
    ex_p_ok = _f(obj.get("exit_price")) is not None
    has_full_exit = bool(ex_t_ok and ex_p_ok)

    return {
        "show_submitted_vline": True,
        "show_entry_hline": (_f(obj.get("entry")) is not None) or sk,
        "entry_is_potential_only": sk,
        "show_stop_hline": _f(obj.get("stop")) is not None,
        "show_target_hline": _f(obj.get("target")) is not None,
        "show_exit": has_full_exit,
    }


def _write_matplotlib_chart(
    bars: list[BarRow],
    outfile: Path,
    *,
    anchor: datetime,
    symbol: str,
    direction: str,
    planned_rr: float | None,
    entry: float | None,
    stop: float | None,
    target: float | None,
    qty: float | None,
    submitted: bool,
    skipped: bool,
    submitted_to_broker: bool,
    bracket_complete: bool,
    signal_category: str,
    skipped_human_one: str,
    locale: str = "en",
    exit_price: float | None = None,
    exit_time_utc: datetime | None = None,
    show_exit_missing_note: bool = False,
    ict_htf: bool | None = None,
    ict_5m: bool | None = None,
    ict_1m: bool | None = None,
    zh_locale: bool = False,
    structure_notes: str = "",
    recon_status: str | None = None,
    realized_r_trade: float | None = None,
) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as exc:  # pragma: no cover - env without mpl
        return f"MPL_UNAVAILABLE: {exc}"

    def _tr(x: bool | None, ok: str, bad: str, unk: str) -> str:
        if x is True:
            return ok
        if x is False:
            return bad
        return unk

    hk = zh_locale
    _EN = {
        "curve": "Close",
        "submitted": "Submitted",
        "entry": "Entry",
        "potential": "Potential entry",
        "stop": "Stop",
        "target": "Target",
        "exit": "Exit / close",
        "exit_missing": "Exit not recorded yet",
        "risk": "Risk zone",
        "reward": "Reward zone",
        "ict_prefix": "ICT:",
        "status_line": "Status:",
        "not_filled_yet": "Not filled yet",
        "realized_r": "Realized R",
    }
    _ZH = {
        "curve": "收盘",
        "submitted": "提交",
        "entry": "入场",
        "potential": "潜在入场",
        "stop": "止损",
        "target": "目标",
        "exit": "平仓",
        "exit_missing": "尚未记录平仓",
        "risk": "风险区",
        "reward": "盈利区",
        "ict_prefix": "ICT：",
        "status_line": "状态：",
        "not_filled_yet": "尚未成交",
        "realized_r": "已实现 R",
    }

    def lab(key: str) -> str:
        return (_ZH if hk else _EN)[key]

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

    x_min, x_max = xs[0], xs[-1]
    fig, ax = plt.subplots(figsize=(11, 5), dpi=120)
    ax.plot(xs, ys, color="#3b82f6", linewidth=1.2, label=lab("curve"))

    anchor_ny = anchor.astimezone(_NY)
    ax.axvline(anchor_ny, color="#eab308", linewidth=1.2, linestyle="--", alpha=0.95)
    ax.text(
        anchor_ny,
        max(ys),
        f"  {lab('submitted')} {anchor_ny.strftime('%H:%M')}",
        fontsize=8,
        color="#a16207",
        va="top",
        ha="left",
    )

    exit_ny: datetime | None = None
    if exit_time_utc is not None:
        exit_ny = exit_time_utc.astimezone(_NY)
        if x_min <= exit_ny <= x_max:
            ax.axvline(exit_ny, color="#a855f7", linewidth=1.1, linestyle="--", alpha=0.95)

    y_min = min(ys)
    y_max = max(ys)
    pad = max((y_max - y_min) * 0.05, 0.05)

    entry_for_zones = entry
    if entry_for_zones is not None and stop is not None:
        y_lo = min(entry_for_zones, stop)
        y_hi = max(entry_for_zones, stop)
        ax.axhspan(y_lo - pad * 0.1, y_hi + pad * 0.1, facecolor="#ef4444", alpha=0.12, label=lab("risk"))

    if entry_for_zones is not None and target is not None:
        y_lo = min(entry_for_zones, target)
        y_hi = max(entry_for_zones, target)
        ax.axhspan(y_lo - pad * 0.1, y_hi + pad * 0.1, facecolor="#22c55e", alpha=0.12, label=lab("reward"))

    ent_lbl = lab("potential") if skipped else lab("entry")

    horiz_specs: list[tuple[float | None, str, str]] = []
    if entry is not None:
        horiz_specs.append((entry, "#f97316", ent_lbl))
    elif skipped:
        pass
    if stop is not None:
        horiz_specs.append((stop, "#dc2626", lab("stop")))
    if target is not None:
        horiz_specs.append((target, "#16a34a", lab("target")))
    has_exit_annotation = exit_price is not None and exit_time_utc is not None
    if has_exit_annotation:
        horiz_specs.append((exit_price, "#a855f7", lab("exit")))

    for y, color, lbl in horiz_specs:
        if y is None:
            continue
        ax.axhline(y=y, color=color, linewidth=1.1, linestyle="-", alpha=0.95)
        ax.text(xs[0], y, f" {lbl} {y:.2f}", color=color, fontsize=9, va="bottom", ha="left")

    lr = (
        "Long"
        if direction == "long"
        else "Short"
        if direction == "short"
        else (direction.title() if direction else "—")
    )

    sub_bits: list[str] = []
    if "DAY_TRADE_READY_STRICT" in (signal_category or ""):
        sub_bits.append("ICT/SMC strict" if not hk else "ICT/SMC 严格")
    elif "DAY_TRADE_READY_AGGRESSIVE" in (signal_category or ""):
        sub_bits.append("ICT/SMC aggressive" if not hk else "ICT/SMC 激进")
    if planned_rr is not None:
        sub_bits.append(f"R/R {planned_rr:g}")
    sub_bits.append(
        ("Protection complete" if bracket_complete else "Protection incomplete")
        if not hk
        else ("保护完整" if bracket_complete else "保护不完整")
    )
    subtitle_mid = " | ".join(sub_bits)

    ict_bits = [
        f"HTF {_tr(ict_htf, 'OK', 'no', '?')}",
        f"5m {_tr(ict_5m, 'OK', 'no', '?')}",
        f"1m {_tr(ict_1m, 'OK', 'no', '?')}",
    ]
    sc_u = str(signal_category or "").upper()
    if "STRICT" in sc_u:
        ict_bits.append("strict")
    elif "AGGRESSIVE" in sc_u:
        ict_bits.append("aggressive")
    ict_line = lab("ict_prefix") + " " + " · ".join(ict_bits)
    struct_line = ""
    if structure_notes.strip():
        struct_line = ("Structure: " if not hk else "结构：") + structure_notes.strip()

    if skipped:
        title_line = f"{symbol} {lr} — Skipped" if not hk else f"{symbol} {lr} — 已跳过"
    elif submitted_to_broker and not submitted:
        title_line = f"{symbol} {lr} — Partial" if not hk else f"{symbol} {lr} — 部分成交"
    elif (submitted or submitted_to_broker) and not bracket_complete:
        title_line = (
            f"{symbol} {lr} — Protection incomplete"
            if not hk
            else f"{symbol} {lr} — 保护不完整"
        )
    elif submitted:
        title_line = f"{symbol} {lr} — Sent" if not hk else f"{symbol} {lr} — 已提交"
    else:
        title_line = f"{symbol} {lr} — Not sent" if not hk else f"{symbol} {lr} — 未提交"

    st_key = title_line.split("—", 1)[-1].strip() if "—" in title_line else title_line
    extra_lines: list[str] = [ict_line]
    if struct_line:
        extra_lines.append(struct_line)
    extra_lines.extend(
        [
            subtitle_mid,
            f"{lab('submitted')}: {anchor_ny.strftime('%Y-%m-%d %H:%M %Z')}",
            f"{lab('status_line')} {st_key}",
        ]
    )
    if skipped and skipped_human_one:
        extra_lines.append(("Skipped: " if not hk else "跳过：") + skipped_human_one)
    elif (submitted or submitted_to_broker) and not bracket_complete:
        extra_lines.append(
            "Bracket protection incomplete — verify in TWS"
            if not hk
            else "括号保护不完整 — 请在 TWS 中核实"
        )
    rs_raw = (recon_status or "").strip()
    if rs_raw == "submitted_not_filled":
        extra_lines.append(lab("not_filled_yet"))
    elif rs_raw == "filled_open":
        extra_lines.append(lab("exit_missing"))
    elif rs_raw == "closed" and realized_r_trade is not None:
        extra_lines.append(f"{lab('realized_r')}: {realized_r_trade:g}")
    elif show_exit_missing_note:
        extra_lines.append(lab("exit_missing"))

    title_blob = title_line + "\n" + "\n".join(extra_lines)
    if qty is not None:
        title_blob += f"\nqty={qty:g}"
    fig.subplots_adjust(top=0.78)
    fig.suptitle(title_blob, fontsize=9, va="top")
    plt.xlabel("NY time" if not hk else "纽约时间")
    plt.ylabel("Price" if not hk else "价格")
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
