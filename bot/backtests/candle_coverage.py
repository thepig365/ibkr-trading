"""Read-only 1-minute candle cache coverage for backtests (Prompt 13BT-UI-DATA).

Uses only :mod:`bot.backtests.candle_cache` and the filesystem. No IBKR, no
broker, no order/execution code.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .candle_cache import DATE_RE, read_candles_csv, SYMBOL_RE, cache_dir_for

# Core basket for `candle-coverage --core-basket` (15 names).
CORE_BASKET: tuple[str, ...] = (
    "AAPL",
    "AMD",
    "NVDA",
    "TSLA",
    "CRM",
    "AMZN",
    "MSFT",
    "META",
    "QQQ",
    "SPY",
    "AVGO",
    "MU",
    "ARM",
    "PLTR",
    "TSM",
)

# Must match the cache layout (1–5 UPPER A–Z, same as :data:`candle_cache.SYMBOL_RE`).
WATCHLIST_DIRNAME = "data/watchlists"
WATCHLIST_GLOB = "*-dynamic-watchlist.json"


def _norm_symbols(symbols: list[str] | None) -> list[str]:
    if not symbols:
        return []
    out: list[str] = []
    for raw in symbols:
        s = (raw or "").strip().upper()
        if not s or not SYMBOL_RE.match(s):
            continue
        if s not in out:
            out.append(s)
    return out


def _validate_dates(start: str, end: str) -> tuple[str, str]:
    if not DATE_RE.match((start or "").strip()) or not DATE_RE.match((end or "").strip()):
        raise ValueError("start and end must be YYYY-MM-DD")
    a = (start or "").strip()
    b = (end or "").strip()
    d0 = datetime.strptime(a, "%Y-%m-%d").date()
    d1 = datetime.strptime(b, "%Y-%m-%d").date()
    if d0 > d1:
        raise ValueError("start must be on or before end")
    return a, b


def us_weekday_trading_days(start: str, end: str) -> list[str]:
    """Weekdays (Mon–Fri) in [start, end] inclusive.

    This does **not** remove US market holidays. ``notes`` in the report
    should mention that limitation.
    """
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out: list[str] = []
    cur = d0
    while cur <= d1:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur = cur + timedelta(days=1)
    return out


def _day_has_bars(path: Path) -> bool:
    if not path.is_file():
        return False
    rows = read_candles_csv(path)
    return len(rows) > 0


def _per_symbol(
    project_root: Path,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    trading_days: list[str],
) -> dict[str, Any]:
    cdir = cache_dir_for(project_root, symbol, timeframe)
    if not cdir.is_dir():
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "requested_start": start,
            "requested_end": end,
            "has_cache": False,
            "cached_start": None,
            "cached_end": None,
            "covered_trading_days": 0,
            "missing_trading_days": list(trading_days),
            "coverage_pct": 0.0,
            "status": "missing",
            "recommended_action": "fetch_missing",
            "notes": [
                "no cache directory for this symbol/timeframe; nothing to backtest in range."
            ],
        }

    days_with_file_data: list[str] = []
    days_missing: list[str] = []
    malformed_days: list[str] = []
    for day in trading_days:
        fpath = cdir / f"{day}.csv"
        if not fpath.is_file():
            days_missing.append(day)
            continue
        if not _day_has_bars(fpath):
            malformed_days.append(day)
            days_missing.append(day)
            continue
        days_with_file_data.append(day)

    n_req = max(len(trading_days), 1)
    covered = len(days_with_file_data)
    cov_pct = round(100.0 * float(covered) / float(n_req if trading_days else 1), 1)

    cached_start: str | None
    cached_end: str | None
    if days_with_file_data:
        cached_start = min(days_with_file_data)
        cached_end = max(days_with_file_data)
    else:
        # Scan any dated CSVs in directory for hints
        stems = [p.stem for p in cdir.glob("*.csv") if DATE_RE.match(p.stem)]
        cached_start = min(stems) if stems else None
        cached_end = max(stems) if stems else None

    if not trading_days:
        status = "missing"
        rec = "insufficient_data"
    elif covered == len(trading_days) and len(trading_days) > 0:
        status = "ready"
        rec = "run_backtest"
    elif covered == 0:
        status = "missing"
        rec = "fetch_missing"
    else:
        status = "partial"
        rec = "fetch_missing"

    notes: list[str] = [
        "US market holidays are not removed from the requested weekday set; expect minor over-counting of expected days."
    ]
    if malformed_days:
        notes.append(
            f"empty or malformed cache files (no valid bars) on: {', '.join(malformed_days[:10])}"
            + ("…" if len(malformed_days) > 10 else "")
        )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start": start,
        "requested_end": end,
        "has_cache": bool(days_with_file_data or (cached_start and cached_end)),
        "cached_start": cached_start,
        "cached_end": cached_end,
        "covered_trading_days": covered,
        "missing_trading_days": days_missing,
        "coverage_pct": cov_pct,
        "status": status,
        "recommended_action": rec,
        "notes": notes,
    }


def check_candle_coverage(
    symbols: list[str],
    start: str,
    end: str,
    *,
    timeframe: str = "1min",
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Analyse 1m candle files under ``data/candles`` for a date range.

    Read-only; no network, no IBKR, no order path.
    """
    if (timeframe or "").strip().lower() not in ("1min", "1m"):
        raise ValueError("Only timeframe 1min is supported for this check.")

    root = Path(project_root or Path.cwd()).resolve()
    start_s, end_s = _validate_dates(start, end)
    syms = _norm_symbols(symbols)
    if not syms:
        return {
            "requested_start": start_s,
            "requested_end": end_s,
            "timeframe": "1min",
            "total_symbols": 0,
            "ready_count": 0,
            "partial_count": 0,
            "missing_count": 0,
            "symbols_ready": [],
            "symbols_partial": [],
            "symbols_missing": [],
            "overall_status": "missing",
            "per_symbol": {},
            "trading_day_count": 0,
            "will_backtest_be_complete": False,
            "backtest_completeness_note": "No symbols after normalisation; nothing to run.",
            "notes": ["Provide at least one valid US symbol (1–5 letters A–Z) or use --core-basket / --watchlist."],
        }

    tdays = us_weekday_trading_days(start_s, end_s)
    if not tdays:
        return {
            "requested_start": start_s,
            "requested_end": end_s,
            "timeframe": "1min",
            "total_symbols": len(syms),
            "ready_count": 0,
            "partial_count": 0,
            "missing_count": len(syms),
            "symbols_ready": [],
            "symbols_partial": [],
            "symbols_missing": list(syms),
            "overall_status": "missing",
            "per_symbol": {
                s: {
                    "symbol": s,
                    "timeframe": "1min",
                    "requested_start": start_s,
                    "requested_end": end_s,
                    "status": "missing",
                    "recommended_action": "insufficient_data",
                    "notes": ["No weekdays in range (weekend-only window or empty)."],
                }
                for s in syms
            },
            "trading_day_count": 0,
            "will_backtest_be_complete": False,
            "backtest_completeness_note": "No US weekday trading days in selected range.",
            "notes": [],
        }
    per: dict[str, Any] = {}
    for s in syms:
        per[s] = _per_symbol(root, s, "1min", start_s, end_s, tdays)

    ready = [k for k, v in per.items() if v["status"] == "ready"]
    partial = [k for k, v in per.items() if v["status"] == "partial"]
    missing = [k for k, v in per.items() if v["status"] == "missing"]

    if len(ready) == len(syms) and len(syms) > 0:
        overall = "ready"
    elif len(missing) == len(syms) and len(syms) > 0:
        overall = "missing"
    else:
        overall = "partial"

    complete = len(ready) == len(syms) and len(syms) > 0 and not partial and not missing
    if complete:
        back_note = "All listed symbols have 1m cache for every weekday in range (holidays not excluded)."
    elif not ready:
        back_note = "Backtest will only include symbols with cache; many names may be skipped (incomplete multi-symbol run)."
    else:
        back_note = "Backtest can run but will be incomplete until missing weekdays are filled for every symbol (see Partial/Missing)."

    return {
        "requested_start": start_s,
        "requested_end": end_s,
        "timeframe": "1min",
        "total_symbols": len(syms),
        "ready_count": len(ready),
        "partial_count": len(partial),
        "missing_count": len(missing),
        "symbols_ready": ready,
        "symbols_partial": partial,
        "symbols_missing": missing,
        "overall_status": overall,
        "per_symbol": per,
        "trading_day_count": len(tdays),
        "will_backtest_be_complete": complete,
        "backtest_completeness_note": back_note,
        "notes": [
            "Weekday-only range (Mon–Fri). NYSE holidays are not modelled; treat coverage_pct as a lower bound for real RTH session coverage."
        ],
    }


def load_latest_watchlist_symbols(project_root: Path) -> tuple[list[str], str | None, str | None]:
    """Return (symbols, path_or_none, error_or_none) from the newest dynamic file.

    Does not import IBKR. Reads ``data/watchlists/*-dynamic-watchlist.json`` only.
    """
    wdir = project_root / WATCHLIST_DIRNAME
    if not wdir.is_dir():
        return [], None, f"no {WATCHLIST_DIRNAME!r} directory"
    cands = sorted(wdir.glob(WATCHLIST_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        return [], None, "no *-dynamic-watchlist.json file found (build-watchlist not run?)"
    path = cands[0]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return [], str(path), f"invalid JSON: {exc}"
    if not isinstance(raw, dict):
        return [], str(path), "watchlist root must be a JSON object"
    rows = raw.get("symbols")
    if not isinstance(rows, list):
        return [], str(path), "no symbols list in watchlist JSON"
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = (row.get("symbol") or "").strip().upper()
        if sym and SYMBOL_RE.match(sym) and sym not in out:
            out.append(sym)
    if not out:
        return [], str(path), "watchlist JSON has no valid symbols"
    return out, str(path), None


__all__ = [
    "CORE_BASKET",
    "check_candle_coverage",
    "load_latest_watchlist_symbols",
    "us_weekday_trading_days",
]
