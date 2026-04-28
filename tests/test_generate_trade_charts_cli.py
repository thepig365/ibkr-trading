"""CLI generate-trade-charts (local cache only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from bot.cli import app

_NY = ZoneInfo("America/New_York")


def _write_project_with_trade(tmp_project: Path) -> str:
    import json as jmod

    from bot.journal_trade_id import compute_stable_trade_row_id

    sym = "CLIT"
    anchor = datetime(2026, 4, 29, 11, 30, tzinfo=_NY)
    row = {
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
        "order_ids": [1, 2, 3],
    }
    pod = tmp_project / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lf = pod / "2026-04-29-intraday-paper-orders.jsonl"
    lf.write_text(jmod.dumps(row) + "\n", encoding="utf-8")
    tid = compute_stable_trade_row_id(str(lf.resolve()), 0, row)
    ny_day = "2026-04-29"
    start = anchor - timedelta(minutes=35)
    lines = ["timestamp,open,high,low,close,volume"]
    for i in range(90):
        t = start + timedelta(minutes=i)
        lines.append(f"{t.isoformat(timespec='seconds')},1,1,1,{10.0 + i * 0.01:.4f},100")
    csv_p = tmp_project / "data" / "candles" / sym / "1min" / f"{ny_day}.csv"
    csv_p.parent.mkdir(parents=True, exist_ok=True)
    csv_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tid


def test_generate_trade_charts_cli_latest_json(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_with_trade(tmp_project)
    env = {**os.environ, "BOT_PROJECT_ROOT": str(tmp_project)}
    r = CliRunner().invoke(
        app,
        ["generate-trade-charts", "--latest", "--limit", "5", "--json"],
        env=env,
    )
    assert r.exit_code == 0, r.output
    summary = json.loads(r.stdout)
    assert "generated_count" in summary
    assert "chart_dir" in summary
    assert "data/reports/trade_charts" in (summary.get("chart_dir") or "")


def test_cli_batch_does_not_import_broker() -> None:
    import bot.journal_trade_charts_pipeline as jtp

    src = Path(jtp.__file__).read_text()
    assert "from bot.broker" not in src
    assert "ibkr_client" not in src.lower()
