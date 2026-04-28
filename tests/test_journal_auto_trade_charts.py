"""Journal auto trade chart pipeline (local cache only)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot.journal_trade_charts_pipeline import (
    journal_chart_cell,
    journal_page_auto_ensure_row_charts,
    generate_trade_charts_batch,
)
from bot.journal_trade_id import compute_stable_trade_row_id

_NY = ZoneInfo("America/New_York")


def _minute_csv(path: Path, rows: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    for ts, c in rows:
        lines.append(f"{ts},1,1,1,{c:.4f},100")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def done_trade_project(tmp_path: Path) -> tuple[Path, str, str]:
    sym = "AUTO"
    anchor = datetime(2026, 4, 28, 10, 0, tzinfo=_NY)
    ts_iso = anchor.isoformat(timespec="seconds")
    row = {
        "timestamp": ts_iso,
        "symbol": sym,
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": True,
        "submitted_to_broker": True,
        "skipped_reasons": [],
        "entry": 50.0,
        "stop": 49.0,
        "target": 52.0,
        "bracket_integrity": "complete",
        "order_ids": [1, 2, 3],
    }
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lf = pod / "2026-04-28-intraday-paper-orders.jsonl"
    lf.write_text(json.dumps(row) + "\n", encoding="utf-8")
    abs_p = str(lf.resolve())
    tid = compute_stable_trade_row_id(abs_p, 0, row)

    ny_day = "2026-04-28"
    start_bar = anchor - timedelta(minutes=40)
    pts = []
    for i in range(100):
        t = start_bar + timedelta(minutes=i)
        pts.append((t.isoformat(timespec="seconds"), 50.0 + i * 0.01))
    _minute_csv(tmp_path / "data" / "candles" / sym / "1min" / f"{ny_day}.csv", pts)

    return tmp_path, tid, ts_iso


def test_journal_chart_cell_skipped_is_na(tmp_path: Path) -> None:
    from types import SimpleNamespace

    r = SimpleNamespace(
        trade_id="a" * 24,
        skipped_reasons=["x"],
        submitted=False,
        submitted_to_broker=False,
        bracket_integrity="",
    )
    jc = journal_chart_cell(tmp_path, r)
    assert jc.tier == "not_applicable"


def test_batch_generates_png(done_trade_project: tuple[Path, str, str]) -> None:
    tmp_path, tid, _ts = done_trade_project
    out = generate_trade_charts_batch(tmp_path, mode_latest=True, limit=5)
    assert out.get("generated_count", 0) >= 1
    p = tmp_path / "data" / "reports" / "trade_charts" / f"{tid}.png"
    assert p.is_file()


def test_journal_page_auto_ensure_creates_chart(
    done_trade_project: tuple[Path, str, str],
) -> None:
    from types import SimpleNamespace

    tmp_path, tid, ts_iso = done_trade_project
    r = SimpleNamespace(
        trade_id=tid,
        skipped_reasons=[],
        submitted=True,
        submitted_to_broker=True,
        bracket_integrity="complete",
        symbol="AUTO",
        timestamp=ts_iso,
    )
    assert journal_page_auto_ensure_row_charts(tmp_path, [r]) >= 1
    assert (tmp_path / "data" / "reports" / "trade_charts" / f"{tid}.png").is_file()
