"""full-auto-paper-readiness (read-only)."""

from __future__ import annotations

from pathlib import Path

from bot.config import load_config
from bot.full_auto_paper_readiness import (
    build_full_auto_paper_readiness,
    in_trading_window_full,
)


def test_readiness_ui_safe_has_no_ibkr_probe_keys(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    r = build_full_auto_paper_readiness(
        tmp_project, cfg, None, probe_ibkr=False, session="full", ui_safe=True
    )
    assert "ok" in r
    assert "status" in r
    assert "blockers" in r
    assert "session_window" in r
    assert r.get("ibkr_connected") is None
    # Monday 9:00 NY — may or may not be in window; structure only
    assert isinstance(r.get("tws_listening"), (bool, type(None)))


def test_morning_window_flag_saturday() -> None:
    assert in_trading_window_full(5, 10 * 60) is False  # Saturday
    assert in_trading_window_full(0, 10 * 60) is True  # Mon 10:00
