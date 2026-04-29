"""Full-auto supervisor includes TWS health alert hook (smoke)."""

from __future__ import annotations

import pathlib


def test_supervisor_source_references_tws_alert_hook() -> None:
    """Static check: hook not removed by accident."""
    root = pathlib.Path(__file__).resolve().parents[1] / "bot" / "full_auto_paper_supervisor.py"
    text = root.read_text(encoding="utf-8")
    assert "check_tws_health_for_alerts" in text
    assert "maybe_send_tws_health_alert" in text
