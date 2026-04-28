"""Tradervue-style trade chart completion: ledger → cache check → optional IBKR 1m fetch → PNG.

* ``fetch_missing_candles=True`` may connect read-only (roster ``candles``) — never places orders.
* UI / normal GET handlers must not call this with fetch; use CLI or explicit UI button only.
* EOD (``automatic_paper_engine`` report-on-exit) may call with fetch per ``settings.trading.trade_charts``.

See :func:`complete_trade_charts` for the pipeline summary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import AppConfig, load_config
from .journal_trade_charts_pipeline import (
    TRADE_CHART_BATCH_RUNTIME_RELPATH,
    ny_date_iso_for_trade_dict,
)
from .trade_journal_chart import (
    candles_available_for_trade,
    generate_trade_journal_chart_png,
    trade_anchor_utc,
    trade_review_chart_png_path,
)
from .trade_ledger import TradeLedgerRecord, build_trade_records

logger = logging.getLogger(__name__)

FetchMode = Literal["local_only", "ibkr_readonly"]


def _effective_fetch(
    fetch_missing_candles: bool,
    fetch_mode: FetchMode | str | None,
) -> bool:
    if fetch_mode == "local_only":
        return False
    if fetch_mode == "ibkr_readonly":
        return True
    return bool(fetch_missing_candles)


def _trade_eligible_for_chart(rec: TradeLedgerRecord) -> bool:
    """Include skipped / submitted / open / closed / protection incomplete rows with anchor + symbol."""

    sym = (rec.symbol or "").strip().upper()
    if not sym:
        return False
    if trade_anchor_utc(rec.raw_json) is None:
        return False

    st = rec.status_slug
    if st == "rejected":
        return False
    if st in {"open", "closed", "pending", "protection_incomplete", "skipped"}:
        return True
    if st == "unknown":
        return bool(rec.raw_json.get("submitted") or rec.submitted_to_broker)
    return False


def _meaningful_planned(rec: TradeLedgerRecord) -> bool:
    return any(
        x is not None
        for x in (rec.entry_price, rec.stop_price, rec.target_price, rec.raw_json.get("entry"))
    )


def _select_records(
    rows: list[TradeLedgerRecord],
    *,
    date_iso: str | None,
    latest: bool,
    limit: int,
    symbols: list[str] | None,
) -> list[TradeLedgerRecord]:
    """Newest-first subset after filters."""

    sym_filter = None
    if symbols:
        sym_filter = {s.strip().upper() for s in symbols if (s or "").strip()}

    cand: list[TradeLedgerRecord] = []
    for rec in rows:
        if not _trade_eligible_for_chart(rec):
            continue
        if rec.status_slug == "unknown" and not _meaningful_planned(rec):
            continue
        if sym_filter and (rec.symbol or "").upper() not in sym_filter:
            continue
        if date_iso:
            d = ny_date_iso_for_trade_dict(rec.raw_json)
            if d != date_iso:
                continue
        cand.append(rec)

    if latest:
        cand = cand[: max(1, limit)]
    else:
        cand = cand[: max(1, limit)]
    return cand


def _fetch_1min_symbol_date(
    cfg: AppConfig,
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    use_rth: bool = True,
) -> bool:
    """Read-only IBKR historical bars → ``data/candles/.../1min``. Returns True if cache likely updated."""

    from datetime import datetime

    from .backtests.candle_cache import CandleCacheError, save_candles_csv
    from .ibkr_connection import connect_readonly_roster_retry
    from .smc_timeframes import resolve_timeframe_spec

    sym = (symbol or "").strip().upper()
    timeframe = "1min"
    spec = resolve_timeframe_spec(timeframe, cfg)
    d0 = datetime.strptime(start_date, "%Y-%m-%d")
    d1 = datetime.strptime(end_date, "%Y-%m-%d")
    days = max((d1 - d0).days + 2, 2)
    duration = f"{days} D"
    bar_size = str(spec.bar_size)
    use_rth_flag = bool(use_rth)

    client = None
    try:
        oc = connect_readonly_roster_retry(cfg, "candles")
        if oc.live_blocked is not None:
            logger.warning("trade_chart_completion: live blocked: %s", oc.live_blocked)
            return False
        if oc.client is None:
            logger.warning("trade_chart_completion: connect failed: %s", oc.fatal_message)
            return False
        client = oc.client
        bars: list[dict] = []
        try:
            bars = client.get_intraday_bars(
                sym,
                duration=duration,
                bar_size=bar_size,
                what_to_show=str(spec.what_to_show or "TRADES"),
                use_rth=use_rth_flag,
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("trade_chart_completion: get_intraday_bars %s: %s", sym, exc)
            bars = []
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    if not bars:
        return False
    root = Path(cfg.absolute(""))
    try:
        stats = save_candles_csv(
            project_root=root,
            symbol=sym,
            timeframe=timeframe,
            bars=bars,
            start=start_date,
            end=end_date,
            force=False,
        )
    except CandleCacheError as exc:
        logger.warning("trade_chart_completion: save_candles_csv: %s", exc)
        return False
    return bool(stats.rows_written or stats.days_written or stats.files)


@dataclass
class TradeChartCompletionSummary:
    """Serializable result from :func:`complete_trade_charts`."""

    selected_count: int = 0
    symbols_seen: list[str] = field(default_factory=list)
    candle_fetch_attempted_count: int = 0
    candle_fetch_success_count: int = 0
    generated_count: int = 0
    available_count: int = 0
    missing_candles_count: int = 0
    fetched_candles_symbols: list[str] = field(default_factory=list)
    fetch_failed_symbols: list[str] = field(default_factory=list)
    error_count: int = 0
    chart_dir: str = ""
    candle_dir: str = ""
    no_exit_count: int = 0
    closed_count: int = 0
    open_count: int = 0
    skipped_status_count: int = 0
    would_generate_count: int = 0
    would_fetch_count: int = 0
    mode: str = "local_only"
    fetch_mode_resolved: str = "local_only"
    dry_run: bool = False
    fetch_missing_candles: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_full_exit(rec: TradeLedgerRecord) -> bool:
    ex_t = (rec.exit_time or "").strip()
    return bool(ex_t and rec.exit_price is not None)


def complete_trade_charts(
    project_root: Path | str,
    *,
    date: str | None = None,
    latest: bool = False,
    limit: int = 50,
    fetch_missing_candles: bool = False,
    fetch_mode: FetchMode | str | None = None,
    symbols: list[str] | None = None,
    read_only: bool = True,
    dry_run: bool = False,
    before_mins: int = 30,
    after_mins: int = 60,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Build normalized records, optionally fetch missing 1m caches, then generate PNGs.

    IBKR is contacted only when the effective fetch flag is true and local day file is missing.
    ``before_mins`` / ``after_mins`` are passed to :func:`generate_trade_journal_chart_png`.
    """

    _ = read_only
    root = Path(project_root).resolve()
    chart_dir = str((root / "data" / "reports" / "trade_charts").resolve())
    candle_dir = str((root / "data" / "candles").resolve())

    would_fetch_ibkr = _effective_fetch(fetch_missing_candles, fetch_mode)
    do_fetch = would_fetch_ibkr and not bool(dry_run)
    resolved_mode_label = "ibkr_readonly" if would_fetch_ibkr else "local_only"

    summary = TradeChartCompletionSummary(
        chart_dir=chart_dir,
        candle_dir=candle_dir,
        dry_run=bool(dry_run),
        fetch_missing_candles=bool(fetch_missing_candles),
        fetch_mode_resolved=resolved_mode_label,
        mode="ibkr_readonly_fetch" if would_fetch_ibkr else "local_only",
    )

    if not latest and not date:
        out = summary.to_dict()
        out["error"] = "require_latest_or_date"
        return out

    date_iso: str | None = None
    if date:
        date_iso = str(date).strip()[:10]
        if len(date_iso) != 10 or date_iso[4] != "-":
            out = summary.to_dict()
            out["error"] = "invalid_date"
            return out

    rows = build_trade_records(root)
    selected = _select_records(
        rows,
        date_iso=date_iso,
        latest=bool(latest),
        limit=max(1, min(int(limit), 500)),
        symbols=symbols,
    )
    summary.selected_count = len(selected)
    sym_seen: set[str] = set()
    for rec in selected:
        s = (rec.symbol or "").strip().upper()
        if s:
            sym_seen.add(s)
    summary.symbols_seen = sorted(sym_seen)

    app_cfg = cfg if cfg is not None else load_config()
    wb = max(1, min(int(before_mins), 1440))
    wa = max(1, min(int(after_mins), 1440))

    fetched_keys: set[str] = set()
    failed_set: set[str] = set()

    for rec in selected:
        st = rec.status_slug
        if st == "closed":
            summary.closed_count += 1
        elif st == "open":
            summary.open_count += 1
        elif st == "skipped":
            summary.skipped_status_count += 1

        tid = rec.trade_id
        png_path = trade_review_chart_png_path(root, tid)

        if png_path.is_file():
            summary.available_count += 1
            if not _has_full_exit(rec):
                summary.no_exit_count += 1
            continue

        sym = (rec.symbol or "").strip().upper()
        anchor = trade_anchor_utc(rec.raw_json)
        if anchor is None:
            summary.error_count += 1
            continue

        ny_d = ny_date_iso_for_trade_dict(rec.raw_json)
        if not ny_d:
            summary.error_count += 1
            continue

        has_candles = candles_available_for_trade(root, rec.raw_json)
        if dry_run:
            if not has_candles:
                summary.missing_candles_count += 1
                if would_fetch_ibkr:
                    summary.would_fetch_count += 1
            else:
                summary.would_generate_count += 1
            continue

        if not has_candles and do_fetch:
            key = f"{sym}:{ny_d}"
            if key not in fetched_keys:
                summary.candle_fetch_attempted_count += 1
                fetched_keys.add(key)
                try:
                    ok = _fetch_1min_symbol_date(app_cfg, sym, ny_d, ny_d, use_rth=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("trade_chart_completion: fetch failed %s %s: %s", sym, ny_d, exc)
                    ok = False
                if ok:
                    summary.candle_fetch_success_count += 1
                    summary.fetched_candles_symbols.append(sym)
                else:
                    failed_set.add(sym)
                    summary.fetch_failed_symbols.append(sym)
            has_candles = candles_available_for_trade(root, rec.raw_json)

        if not has_candles:
            summary.missing_candles_count += 1
            continue

        try:
            res = generate_trade_journal_chart_png(
                root, tid, force=False, locale="en", before_mins=wb, after_mins=wa
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trade_chart_completion: chart gen failed tid=%s: %s", tid, exc)
            summary.error_count += 1
            continue

        if res.ok and res.png_path is not None:
            low = (res.message or "").lower()
            if "already exists" in low:
                summary.available_count += 1
            else:
                summary.generated_count += 1
                summary.available_count += 1
            if not _has_full_exit(rec):
                summary.no_exit_count += 1
        else:
            low_m = (res.message or "").lower()
            if "no local" in low_m or "expected file" in low_m:
                summary.missing_candles_count += 1
            else:
                summary.error_count += 1

    out_dict = summary.to_dict()
    out_dict["fetch_mode"] = summary.fetch_mode_resolved
    out_dict["fetched_candles_symbols"] = sorted(set(summary.fetched_candles_symbols))
    out_dict["fetch_failed_symbols"] = sorted(set(failed_set))

    rtp = root / TRADE_CHART_BATCH_RUNTIME_RELPATH
    if not dry_run:
        try:
            rtp.parent.mkdir(parents=True, exist_ok=True)
            rtp.write_text(
                json.dumps(out_dict, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.warning("could not persist trade chart completion summary", exc_info=True)

    return out_dict


__all__ = ["complete_trade_charts", "TradeChartCompletionSummary", "_trade_eligible_for_chart"]
