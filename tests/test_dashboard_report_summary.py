"""Dashboard points to Reports hub; no new trading paths in route."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_dashboard_template_report_center_link() -> None:
    t = (REPO / "bot_ui" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert "dashboard.report_center_title" in t
    assert "ret('/reports')" in t
    assert "Email is optional" in t or "optional" in t.lower()


def test_dashboard_route_does_not_import_broker_for_render() -> None:
    p = (REPO / "bot_ui" / "routes" / "dashboard.py").read_text(encoding="utf-8")
    assert "from bot.ibkr" not in p
