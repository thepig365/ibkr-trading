"""Paper strategy cannot be a stub (13STRATEGY-UI)."""

from __future__ import annotations

from pathlib import Path

from bot.strategy_ui import load_strategy_ui_catalog, validate_paper_strategy, validate_per_area


def test_chanlun_paper_rejected() -> None:
    cat = load_strategy_ui_catalog(Path("."))
    ok, err = validate_paper_strategy(cat, "chanlun_intraday_v1")
    assert ok is False
    assert "disabled" in err.lower() or "paper" in err.lower()


def test_ict_paper_ok() -> None:
    cat = load_strategy_ui_catalog(Path("."))
    ok, _ = validate_paper_strategy(cat, "ict_smc_intraday_v1")
    assert ok is True


def test_validate_scan_chanlun() -> None:
    cat = load_strategy_ui_catalog(Path("."))
    ok, err = validate_per_area(cat, "scan", "chanlun_intraday_v1")
    assert ok is False
    assert "not enabled" in err.lower() or "scan" in err.lower()
