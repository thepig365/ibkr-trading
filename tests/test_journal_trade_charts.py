"""Trade journal chart generator — local CSV only, no broker."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.journal_trade_id import compute_stable_trade_row_id
from bot.trade_journal_chart import generate_trade_journal_chart_png

_NY = ZoneInfo("America/New_York")


def _write_minute_csv(path: Path, rows: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    for ts, c in rows:
        lines.append(f"{ts},1,1,1,{c:.4f},100")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_trade_chart_png_writes_from_local_csv_only(tmp_path: Path) -> None:
    sym = "PLTR"
    anchor = datetime(2026, 4, 24, 9, 40, tzinfo=_NY)
    payload = {
        "timestamp": anchor.isoformat(timespec="seconds"),
        "symbol": sym,
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 100.5,
        "stop": 99.5,
        "target": 102.0,
        "planned_rr": 1.5,
        "quantity": 2,
        "order_ids": [9, 8, 7],
        "paper_only": True,
        "live_trading_allowed": False,
        "bracket_integrity": "complete",
    }
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    logf = pod / "2026-04-24-intraday-paper-orders.jsonl"
    logf.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    abs_p = str(logf.resolve())
    tid = compute_stable_trade_row_id(abs_p, 0, payload)

    ny_day = "2026-04-24"
    csv_p = tmp_path / "data" / "candles" / sym / "1min" / f"{ny_day}.csv"
    start_bar = anchor - timedelta(minutes=45)
    minute_rows = []
    for i in range(120):
        t = start_bar + timedelta(minutes=i)
        iso = t.isoformat(timespec="seconds")
        c = 100.0 + i * 0.01
        minute_rows.append((iso, c))
    _write_minute_csv(csv_p, minute_rows)

    out = generate_trade_journal_chart_png(tmp_path, tid)
    assert out.ok, out.message
    assert out.png_path is not None and out.png_path.is_file()


def test_chart_missing_journal_row_fails_cleanly(tmp_path: Path) -> None:
    out = generate_trade_journal_chart_png(tmp_path, "a" * 22)
    assert not out.ok
