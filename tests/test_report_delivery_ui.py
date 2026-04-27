"""UI routes expose reporting context without calling providers (import-level)."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_research_route_no_ibkr_client_import() -> None:
    p = (REPO / "bot_ui" / "routes" / "research.py").read_text(encoding="utf-8")
    assert "from bot.ibkr" not in p
    assert "import bot.ibkr" not in p


def test_reports_shows_telegram_section_in_template() -> None:
    t = (REPO / "bot_ui" / "templates" / "reports.html").read_text(encoding="utf-8")
    assert "Telegram" in t and "news-monitor-readiness" in t
