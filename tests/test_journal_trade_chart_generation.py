"""Trade journal chart generator — local CSV only, no broker."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from bot.cli import app
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


def _sample_trade_project(tmp_project: Path) -> str:
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
    pod = tmp_project / "data" / "paper_orders"
    pod.mkdir(parents=True)
    logf = pod / "2026-04-24-intraday-paper-orders.jsonl"
    logf.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    abs_p = str(logf.resolve())
    tid = compute_stable_trade_row_id(abs_p, 0, payload)

    ny_day = "2026-04-24"
    csv_p = tmp_project / "data" / "candles" / sym / "1min" / f"{ny_day}.csv"
    start_bar = anchor - timedelta(minutes=45)
    minute_rows = []
    for i in range(120):
        t = start_bar + timedelta(minutes=i)
        iso = t.isoformat(timespec="seconds")
        c = 100.0 + i * 0.01
        minute_rows.append((iso, c))
    _write_minute_csv(csv_p, minute_rows)
    return tid


def test_generate_trade_chart_cli_json_writes_under_reports(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_PROJECT_ROOT", str(tmp_project))
    tid = _sample_trade_project(tmp_project)
    env = {**os.environ, "BOT_PROJECT_ROOT": str(tmp_project)}
    r = CliRunner().invoke(
        app,
        ["generate-trade-chart", "--trade-id", tid, "--json", "--force"],
        env=env,
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] in {"generated", "already_exists"}
    cp = (payload.get("chart_path") or "").replace("\\", "/")
    assert "data/reports/trade_charts" in cp
    assert cp.endswith(f"{tid}.png")

    r2 = CliRunner().invoke(
        app,
        ["journal-generate-trade-chart", "--trade-id", tid, "--json"],
        env=env,
    )
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.stdout)["status"] == "already_exists"


def test_generate_trade_chart_cli_missing_candles_json(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_PROJECT_ROOT", str(tmp_project))
    sym = "NOCACHE"
    anchor = datetime(2026, 4, 24, 9, 40, tzinfo=_NY)
    payload = {
        "timestamp": anchor.isoformat(timespec="seconds"),
        "symbol": sym,
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 10.0,
        "stop": 9.0,
        "target": 12.0,
        "bracket_integrity": "complete",
    }
    pod = tmp_project / "data" / "paper_orders"
    pod.mkdir(parents=True)
    logf = pod / "2026-04-24-intraday-paper-orders.jsonl"
    logf.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tid = compute_stable_trade_row_id(str(logf.resolve()), 0, payload)
    env = {**os.environ, "BOT_PROJECT_ROOT": str(tmp_project)}
    r = CliRunner().invoke(
        app,
        ["generate-trade-chart", "--trade-id", tid, "--json"],
        env=env,
    )
    assert r.exit_code == 2, r.output
    out = json.loads(r.stdout)
    assert out["status"] == "missing_candles"
