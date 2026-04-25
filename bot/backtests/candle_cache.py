"""Normalised candle cache for backtests (Prompt 13E PART A).

Layout on disk::

    data/candles/{SYMBOL}/{TIMEFRAME}/{YYYY-MM-DD}.csv

with the canonical CSV schema::

    timestamp,open,high,low,close,volume

* ``timestamp`` is the ISO-8601 string the IBKR client returns
  (``"2026-04-24 09:30:00-04:00"``) — we store it verbatim and never
  attempt to re-tz it. Backtest consumers parse it lazily via
  :func:`datetime.fromisoformat`.
* ``volume`` is float (IBKR sometimes returns fractional aggregated
  volume on intraday bars).

Hard invariants
---------------
* No broker / IBKR import. The cache reader/writer is data-only.
* Daily files are append-and-dedupe: re-running ``fetch-candles`` for
  a day already on disk is a no-op for that day unless ``force=True``.
* Symbols and timeframes are validated against the same allowlists
  used by the live scanner (``A-Z`` 1–5 chars, timeframe in
  :data:`bot.smc_timeframes.SUPPORTED_TIMEFRAMES`).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..smc_timeframes import SUPPORTED_TIMEFRAMES, normalise_timeframe

LOG = logging.getLogger(__name__)

# Canonical CSV header used for every cache file.
CANDLE_CSV_HEADER: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

# Symbol pattern matches the live scanner's validator. Refused symbols
# never reach disk so the cache stays clean.
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
# Date pattern for both filenames and CLI ``--start`` / ``--end``.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CandleCacheError(ValueError):
    """Raised when arguments to the cache layer are invalid."""


@dataclass(frozen=True)
class BarRow:
    """Normalised OHLCV bar (immutable so dedupe sets work)."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "BarRow":
        return cls(
            timestamp=str(row.get("timestamp") or "").strip(),
            open=_to_float(row.get("open")),
            high=_to_float(row.get("high")),
            low=_to_float(row.get("low")),
            close=_to_float(row.get("close")),
            volume=_to_float(row.get("volume")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class CandleCacheStats:
    """Summary returned by :func:`save_candles_csv` and the CLI."""

    symbol: str
    timeframe: str
    cache_dir: Path
    days_written: int = 0
    days_skipped: int = 0
    rows_written: int = 0
    rows_deduped: int = 0
    files: list[Path] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "cache_dir": str(self.cache_dir),
            "days_written": self.days_written,
            "days_skipped": self.days_skipped,
            "rows_written": self.rows_written,
            "rows_deduped": self.rows_deduped,
            "files": [str(p) for p in self.files],
            "gaps": list(self.gaps),
            "notes": list(self.notes),
        }


def _to_float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _validate_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise CandleCacheError(
            f"Invalid symbol {symbol!r}; must match {SYMBOL_RE.pattern}."
        )
    return s


def _validate_timeframe(timeframe: str) -> str:
    """Strict validator (no silent coercion).

    ``normalise_timeframe`` would happily map an unknown label like
    ``"weekly"`` to ``"daily"`` — that's correct for the live scanner
    (which has no other choice) but wrong for the cache, where a
    silent coercion would land bars in the wrong directory and bake a
    silent bug into every downstream backtest. We require the *raw*
    label (or one of the well-known aliases) to be a supported
    timeframe.
    """
    if not timeframe or not isinstance(timeframe, str):
        raise CandleCacheError(
            f"Invalid timeframe {timeframe!r}; supported: {sorted(SUPPORTED_TIMEFRAMES)}."
        )
    raw = timeframe.strip().lower()
    tf = normalise_timeframe(raw)
    if tf not in SUPPORTED_TIMEFRAMES:
        raise CandleCacheError(
            f"Invalid timeframe {timeframe!r}; supported: {sorted(SUPPORTED_TIMEFRAMES)}."
        )
    # Reject silent fallbacks: if the input wasn't a known alias for
    # ``tf``, refuse rather than write to the wrong cache directory.
    aliases_for_tf: dict[str, set[str]] = {
        "daily": {"1d", "d", "daily"},
        "4h": {"4h", "4hr", "4 hrs", "4hours", "240m"},
        "30min": {"30m", "30 min", "30mins", "30 mins", "30min"},
        "5min": {"5m", "5 min", "5mins", "5 mins", "5min"},
        "1min": {"1m", "1 min", "1mins", "1 mins", "1min"},
    }
    if raw not in aliases_for_tf.get(tf, set()):
        raise CandleCacheError(
            f"Invalid timeframe {timeframe!r}; supported: {sorted(SUPPORTED_TIMEFRAMES)}."
        )
    return tf


def _validate_date(d: str) -> str:
    if not DATE_RE.match(d or ""):
        raise CandleCacheError(f"Invalid date {d!r}; expected YYYY-MM-DD.")
    return d


def cache_dir_for(root: Path, symbol: str, timeframe: str) -> Path:
    """Return ``root / data / candles / SYMBOL / TIMEFRAME``.

    ``root`` should be the project root (e.g. ``cfg.absolute("")``).
    The directory is NOT created here — :func:`save_candles_csv`
    creates it lazily.
    """
    sym = _validate_symbol(symbol)
    tf = _validate_timeframe(timeframe)
    return Path(root) / "data" / "candles" / sym / tf


def _bar_date(ts: str) -> str | None:
    """Extract a ``YYYY-MM-DD`` string from a bar timestamp.

    Accepts ``"2026-04-24"``, ``"2026-04-24 09:30:00-04:00"``,
    ``"2026-04-24T09:30:00+00:00"``. Returns ``None`` if it cannot be
    parsed (the CLI surfaces those as gaps/notes; the bar is dropped).
    """
    if not ts:
        return None
    head = ts.replace("T", " ").strip()[:10]
    if DATE_RE.match(head):
        return head
    return None


def _group_by_day(bars: Iterable[Mapping[str, Any]]) -> dict[str, list[BarRow]]:
    out: dict[str, list[BarRow]] = {}
    for raw in bars:
        bar = BarRow.from_mapping(raw)
        day = _bar_date(bar.timestamp)
        if day is None:
            continue
        out.setdefault(day, []).append(bar)
    for day, rows in out.items():
        rows.sort(key=lambda r: r.timestamp)
    return out


def write_csv_for_day(
    cache_dir: Path,
    day: str,
    bars: Sequence[BarRow],
    *,
    force: bool = False,
) -> tuple[Path, int, int, bool]:
    """Write/merge a single day's CSV. Returns (path, rows_written, deduped, was_new).

    * If the file does not exist, write all bars.
    * If it exists and ``force=False``: merge by ``timestamp`` (existing
      bars win — we never overwrite established history).
    * If it exists and ``force=True``: replace with the supplied bars.
    """
    _validate_date(day)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{day}.csv"
    new_bars = [b for b in bars if _bar_date(b.timestamp) == day]
    if not new_bars and not force:
        return path, 0, 0, False

    existing: dict[str, BarRow] = {}
    was_new = not path.exists()
    if path.exists() and not force:
        for r in read_candles_csv(path):
            existing[r.timestamp] = r

    deduped = 0
    if force:
        merged: dict[str, BarRow] = {b.timestamp: b for b in new_bars}
    else:
        merged = dict(existing)
        for b in new_bars:
            if b.timestamp in merged:
                deduped += 1
                continue
            merged[b.timestamp] = b

    rows = sorted(merged.values(), key=lambda r: r.timestamp)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CANDLE_CSV_HEADER)
        for r in rows:
            w.writerow([r.timestamp, r.open, r.high, r.low, r.close, r.volume])
    return path, len(rows) - len(existing) + (0 if force else 0), deduped, was_new


def save_candles_csv(
    project_root: Path,
    symbol: str,
    timeframe: str,
    bars: Iterable[Mapping[str, Any]],
    *,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
) -> CandleCacheStats:
    """Persist ``bars`` into the per-day CSV layout. Reports gaps.

    ``start`` / ``end`` (YYYY-MM-DD) are optional. When supplied, the
    function lists any *missing* trading-day-shaped strings between
    ``start`` and ``end`` for which no bars were saved as ``gaps``.
    Gaps include weekends/holidays — we don't try to be clever here;
    the report is informational.
    """
    sym = _validate_symbol(symbol)
    tf = _validate_timeframe(timeframe)
    out_dir = cache_dir_for(project_root, sym, tf)
    stats = CandleCacheStats(symbol=sym, timeframe=tf, cache_dir=out_dir)

    grouped = _group_by_day(bars)
    if not grouped:
        stats.notes.append("no rows returned for this symbol/timeframe range.")
        return stats

    for day in sorted(grouped):
        if start and day < start:
            continue
        if end and day > end:
            continue
        path, written, deduped, was_new = write_csv_for_day(
            out_dir, day, grouped[day], force=force
        )
        stats.files.append(path)
        if was_new or written or force:
            stats.days_written += 1
            stats.rows_written += max(written, 0)
        else:
            stats.days_skipped += 1
        stats.rows_deduped += deduped

    if start and end:
        # Walk every calendar day in the requested range and flag the
        # ones we did not write. Consumers (CLI / report) decide
        # whether that is a real gap or a non-trading day.
        try:
            d0 = datetime.strptime(start, "%Y-%m-%d").date()
            d1 = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            d0 = d1 = None
        if d0 and d1 and d0 <= d1:
            present = set(grouped)
            cur = d0
            while cur <= d1:
                s = cur.isoformat()
                if s not in present:
                    stats.gaps.append(s)
                cur = cur.fromordinal(cur.toordinal() + 1)
    return stats


def read_candles_csv(path: Path) -> list[BarRow]:
    """Read one day's CSV. Returns ``[]`` if the file is missing/malformed."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[BarRow] = []
    try:
        with p.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = (row.get("timestamp") or "").strip()
                if not ts:
                    continue
                try:
                    out.append(
                        BarRow(
                            timestamp=ts,
                            open=float(row.get("open") or 0.0),
                            high=float(row.get("high") or 0.0),
                            low=float(row.get("low") or 0.0),
                            close=float(row.get("close") or 0.0),
                            volume=float(row.get("volume") or 0.0),
                        )
                    )
                except (TypeError, ValueError):
                    continue
    except OSError as exc:
        LOG.debug("read_candles_csv: %s: %s", p, exc)
        return []
    out.sort(key=lambda r: r.timestamp)
    return out


def load_candles(
    project_root: Path,
    symbol: str,
    timeframe: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[BarRow]:
    """Load all bars for ``symbol``/``timeframe`` between ``start`` and ``end`` inclusive.

    Missing cache directory or empty range returns ``[]`` — the CLI is
    responsible for surfacing the "Missing candle cache. Run
    fetch-candles first." message.
    """
    if start is not None:
        _validate_date(start)
    if end is not None:
        _validate_date(end)
    out_dir = cache_dir_for(project_root, symbol, timeframe)
    if not out_dir.exists():
        return []
    files = sorted(out_dir.glob("*.csv"))
    bars: list[BarRow] = []
    for path in files:
        day = path.stem
        if not DATE_RE.match(day):
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        bars.extend(read_candles_csv(path))
    bars.sort(key=lambda r: r.timestamp)
    return bars


__all__ = [
    "BarRow",
    "CANDLE_CSV_HEADER",
    "CandleCacheError",
    "CandleCacheStats",
    "DATE_RE",
    "SYMBOL_RE",
    "cache_dir_for",
    "load_candles",
    "read_candles_csv",
    "save_candles_csv",
    "write_csv_for_day",
]
