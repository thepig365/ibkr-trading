"""Write/read ``data/candles_forex/{PAIRSLUG}/1min/*.csv`` — same schema as stock cache."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from bot.backtests.candle_cache import (
    BarRow,
    CANDLE_CSV_HEADER,
    DATE_RE,
)

from . import FOREX_CANDLES_ROOT

LOG = logging.getLogger(__name__)


def forex_dir(project_root: Path, pair_slug: str, timeframe: str) -> Path:
    return Path(project_root).resolve() / FOREX_CANDLES_ROOT / pair_slug.upper() / timeframe


def _extract_day(ts_raw: str) -> str | None:
    s = str(ts_raw or "").strip()
    if len(s) >= 10:
        cand = s[:10]
        if DATE_RE.match(cand):
            return cand
    return None


def _group_by_day(bars: Iterable[Mapping[str, Any]]) -> dict[str, list[BarRow]]:
    g: dict[str, list[BarRow]] = defaultdict(list)
    for raw in bars:
        ts = _extract_day(str(raw.get("timestamp") or ""))
        if not ts:
            continue
        try:
            g[ts].append(
                BarRow(
                    timestamp=str(raw.get("timestamp") or ""),
                    open=float(raw.get("open") or 0.0),
                    high=float(raw.get("high") or 0.0),
                    low=float(raw.get("low") or 0.0),
                    close=float(raw.get("close") or 0.0),
                    volume=float(raw.get("volume") or 0.0),
                )
            )
        except (TypeError, ValueError):
            continue
    return dict(g)


def save_forex_candles_csv(
    project_root: Path,
    pair_slug: str,
    timeframe: str,
    bars: Iterable[Mapping[str, Any]],
    *,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    out_dir = forex_dir(project_root, pair_slug, timeframe)
    out_dir.mkdir(parents=True, exist_ok=True)
    grouped = _group_by_day(list(bars))
    files = 0
    rows = 0
    gaps: list[str] = []
    for day in sorted(grouped):
        if start and day < start:
            continue
        if end and day > end:
            continue
        path = out_dir / f"{day}.csv"
        merged: dict[str, BarRow] = {}
        if path.is_file() and not force:
            # merge with existing
            try:
                with path.open("r", newline="", encoding="utf-8") as f:
                    rdr = csv.DictReader(f)
                    for row in rdr:
                        br = BarRow.from_mapping(row)
                        merged[br.timestamp] = br
            except OSError:
                pass
        for br in grouped[day]:
            merged[br.timestamp] = br
        final = sorted(merged.values(), key=lambda r: r.timestamp)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CANDLE_CSV_HEADER)
            for r in final:
                w.writerow([r.timestamp, r.open, r.high, r.low, r.close, r.volume])
        files += 1
        rows += len(final)
    return {
        "cache_dir": str(out_dir),
        "days_written": files,
        "rows_written": rows,
        "gaps": gaps,
    }


def load_forex_candles(
    project_root: Path,
    pair_slug: str,
    timeframe: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[BarRow]:
    out_dir = forex_dir(project_root, pair_slug, timeframe)
    if not out_dir.is_dir():
        return []
    rows: list[BarRow] = []
    for path in sorted(out_dir.glob("*.csv")):
        day = path.stem
        if not DATE_RE.match(day):
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        try:
            with path.open("r", newline="", encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    rows.append(BarRow.from_mapping(row))
        except OSError:
            continue
    rows.sort(key=lambda r: r.timestamp)
    return rows


__all__ = ["save_forex_candles_csv", "load_forex_candles", "forex_dir"]
