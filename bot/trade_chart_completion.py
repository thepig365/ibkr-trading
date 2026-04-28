"""Tradervue-style trade chart completion: ledger → cache check → optional IBKR 1m fetch → PNG.

* ``fetch_missing_candles=True`` may connect read-only (roster ``candles``) — never places orders.
* UI / normal GET handlers must not call this with fetch; use CLI or explicit UI button only.

See :func:`complete_trade_charts` for the pipeline summary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
        # Skip pure noise: unknown without submission and without planned prices
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
    generated_count: int = 0
    available_count: int = 0
    missing_candles_count: int = 0
    fetched_candles_symbols: list[str] = None  # type: ignore[assignment]
    fetch_failed_symbols: list[str] = None  # type: ignore[assignment]
    error_count: int = 0
    chart_dir: str = ""
    no_exit_count: int = 0
    closed_count: int = 0
    open_count: int = 0
    skipped_status_count: int = 0
    would_generate_count: int = 0
    would_fetch_count: int = 0
    mode: str = "local_only"
    dry_run: bool = False
    fetch_missing_candles: bool = False

    def __post_init__(self) -> None:
        if self.fetched_candles_symbols is None:
            self.fetched_candles_symbols = []
        if self.fetch_failed_symbols is None:
            self.fetch_failed_symbols = []

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


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
    symbols: list[str] | None = None,
    read_only: bool = True,
    dry_run: bool = False,
    cfg: AppConfig | None = None,
) -> dict[str, Any]:
    """Build normalized records, optionally fetch missing 1m caches, then generate PNGs.

    IBKR is contacted **only** when ``fetch_missing_candles=True`` and local day file is missing.
    ``read_only`` is reserved for future use; fetches always use read-only historical data paths.

    Returns a JSON-serializable dict (see :class:`TradeChartCompletionSummary`).
    """

    _ = read_only  # explicit API; all implemented fetches are read-only historical bars
    root = Path(project_root).resolve()
    chart_dir = str((root / "data" / "reports" / "trade_charts").resolve())

    summary = TradeChartCompletionSummary(
        chart_dir=chart_dir,
        dry_run=bool(dry_run),
        fetch_missing_candles=bool(fetch_missing_candles),
        mode="ibkr_readonly_fetch" if fetch_missing_candles else "local_only",
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

    app_cfg = cfg if cfg is not None else load_config()

    fetched_set: set[str] = set()
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
                if fetch_missing_candles:
                    summary.would_fetch_count += 1
            else:
                summary.would_generate_count += 1
            continue

        if not has_candles and fetch_missing_candles:
            key = f"{sym}:{ny_d}"
            if key not in fetched_set:
                ok = _fetch_1min_symbol_date(app_cfg, sym, ny_d, ny_d, use_rth=True)
                fetched_set.add(key)
                if ok:
                    summary.fetched_candles_symbols.append(sym)
                else:
                    failed_set.add(sym)
                    summary.fetch_failed_symbols.append(sym)
            has_candles = candles_available_for_trade(root, rec.raw_json)

        if not has_candles:
            summary.missing_candles_count += 1
            continue

        res = generate_trade_journal_chart_png(root, tid, force=False, locale="en")
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

    # Persist last summary for Reports UI (same path as batch)
    out_dict = summary.to_dict()
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
