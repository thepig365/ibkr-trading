"""UI-first reports: no orders, no IBKR import in report routes."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_reports_route_does_not_import_ibkr() -> None:
    p = (REPO / "bot_ui" / "routes" / "reports.py").read_text(encoding="utf-8")
    assert "from bot.ibkr" not in p
    assert "import bot.ibkr" not in p


def test_report_hub_builder_has_no_network() -> None:
    from bot.reports import report_hub_ui

    src = Path(report_hub_ui.__file__).read_text(encoding="utf-8")
    assert "requests." not in src
    assert "httpx" not in src
    assert "urllib.request.urlopen" not in src
