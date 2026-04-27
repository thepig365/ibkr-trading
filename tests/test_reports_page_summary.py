"""Reports page template: hub sections, optional email copy."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from bot.config import load_config
from bot.reports.report_hub_ui import build_report_hub_ui_context

REPO = Path(__file__).resolve().parent.parent


def _install(tmp: Path) -> None:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    for n in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        s = REPO / "config" / n
        if s.is_file():
            shutil.copy(s, tmp / "config" / n)
    (tmp / "data").mkdir(parents=True, exist_ok=True)


def test_report_hub_context_reads_latest_paper_json(tmp_path: Path) -> None:
    _install(tmp_path)
    rdir = tmp_path / "data" / "reports" / "paper"
    rdir.mkdir(parents=True)
    payload = {
        "date": "2026-04-26",
        "data_status": "ok",
        "execution_summary": {"submitted_to_broker_count": 1, "skipped_count": 2},
        "budget": {"today_submitted_notional_usd": 100.0, "daily_remaining_notional_usd": 900.0},
        "safety": {"reconcile_status": "pass"},
    }
    (rdir / "2026-04-26-paper-daily-report.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (rdir / "2026-04-26-paper-daily-report.md").write_text("# Daily\n\nHello\n", encoding="utf-8")
    load_config(project_root=tmp_path)
    ctx = build_report_hub_ui_context(tmp_path)
    assert ctx["hub_paper_daily"] is not None
    assert ctx["hub_paper_daily"]["date"] == "2026-04-26"
    assert "Hello" in (ctx.get("hub_paper_daily_md_excerpt") or "")


def test_reports_template_primary_hub() -> None:
    t = (REPO / "bot_ui" / "templates" / "reports.html").read_text(encoding="utf-8")
    assert "reports.sub" in t
    assert "reports.todays_summary" in t
    assert "Delivery status (optional)" in t
    assert "Email is optional" in t
    assert "Paper trading detail" in t
    assert "Backtest" in t
    assert "Telegram" in t


def test_reports_lists_paper_backtest_news() -> None:
    t = (REPO / "bot_ui" / "templates" / "reports.html").read_text(encoding="utf-8")
    assert "report_hub" in t
    assert "hub_paper_daily" in t
    assert "No market-moving news" in t
