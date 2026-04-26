"""Weekly paper report aggregation (Prompt 13M)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from bot.reports.paper_weekly import build_weekly_paper_report
from bot.reports.render_markdown import render_paper_weekly_markdown


REPO = Path(__file__).resolve().parent.parent


def _install_config(target: Path) -> None:
    (target / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, target / "config" / name)


def test_weekly_aggregates_days(tmp_path: Path) -> None:
    _install_config(tmp_path)
    po = tmp_path / "data" / "paper_orders"
    po.mkdir(parents=True)
    for day in ("2026-03-01", "2026-03-02"):
        p = po / f"{day}-intraday-paper-orders.jsonl"
        p.write_text(
            json.dumps(
                {
                    "symbol": "QQQ",
                    "submitted_to_broker": True,
                    "submitted": True,
                    "bracket_integrity": "complete",
                    "planned_rr": 1.5,
                    "estimated_notional": 1000.0,
                    "quantity": 1,
                    "entry": 400.0,
                    "stop": 399.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    w = build_weekly_paper_report(tmp_path, "2026-03-01", "2026-03-02")
    assert w.get("trading_days_count") == 2
    assert w.get("total_paper_orders") == 2
    assert w.get("total_complete_brackets") == 2
    md = render_paper_weekly_markdown(w)
    assert "# Paper Trading Weekly Report" in md
    assert "QQQ" in md
