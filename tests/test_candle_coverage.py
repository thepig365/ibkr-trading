"""Unit tests for backtest 1m candle coverage (read-only, no IBKR)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.backtests.candle_cache import save_candles_csv, write_csv_for_day
from bot.backtests.candle_coverage import (
    CORE_BASKET,
    check_candle_coverage,
    load_latest_watchlist_symbols,
)


def _one_bar_day(day: str) -> list[dict]:
    return [
        {
            "timestamp": f"{day} 10:00:00-04:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000.0,
        }
    ]


def test_full_cache_returns_ready(tmp_path: Path) -> None:
    for d in ("2026-04-20", "2026-04-21", "2026-04-22"):
        save_candles_csv(tmp_path, "CRM", "1min", _one_bar_day(d), start=None, end=None)
    r = check_candle_coverage(
        ["CRM"],
        "2026-04-20",
        "2026-04-22",
        project_root=tmp_path,
    )
    assert r["overall_status"] == "ready"
    assert r["per_symbol"]["CRM"]["status"] == "ready"
    assert r["per_symbol"]["CRM"]["recommended_action"] == "run_backtest"
    assert r["will_backtest_be_complete"] is True


def test_no_cache_returns_missing(tmp_path: Path) -> None:
    r = check_candle_coverage(
        ["ZZZZ"],
        "2026-04-20",
        "2026-04-24",
        project_root=tmp_path,
    )
    assert r["per_symbol"]["ZZZZ"]["status"] == "missing"
    assert r["missing_count"] == 1


def test_partial_missing_weekdays(tmp_path: Path) -> None:
    save_candles_csv(tmp_path, "AAPL", "1min", _one_bar_day("2026-04-20"), start=None, end=None)
    r = check_candle_coverage(
        ["AAPL"],
        "2026-04-20",
        "2026-04-24",
        project_root=tmp_path,
    )
    assert r["per_symbol"]["AAPL"]["status"] == "partial"
    assert r["per_symbol"]["AAPL"]["recommended_action"] == "fetch_missing"
    assert len(r["per_symbol"]["AAPL"]["missing_trading_days"]) >= 1


def test_empty_csv_treated_as_missing(tmp_path: Path) -> None:
    d = "2026-04-20"
    p = (
        tmp_path
        / "data"
        / "candles"
        / "CRM"
        / "1min"
        / f"{d}.csv"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    r = check_candle_coverage(
        ["CRM"],
        d,
        d,
        project_root=tmp_path,
    )
    assert r["per_symbol"]["CRM"]["status"] == "missing"


def test_malformed_csv_does_not_crash(tmp_path: Path) -> None:
    p = (
        tmp_path
        / "data"
        / "candles"
        / "MU"
        / "1min"
        / "2026-04-20.csv"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not,csv,at,all\nfoo\n", encoding="utf-8")
    r = check_candle_coverage(
        ["MU"],
        "2026-04-20",
        "2026-04-20",
        project_root=tmp_path,
    )
    assert "MU" in r["per_symbol"]


def test_core_basket_constant_len() -> None:
    assert len(CORE_BASKET) == 15


def test_load_watchlist_no_dir(tmp_path: Path) -> None:
    sy, p, err = load_latest_watchlist_symbols(tmp_path)
    assert sy == []
    assert err is not None


def test_check_does_not_import_broker() -> None:
    import bot.backtests.candle_coverage as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    for bad in ("ibkr_client", "bot.broker", "IBKRClient", "ib_async"):
        assert bad not in src

