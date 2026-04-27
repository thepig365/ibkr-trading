"""market-news-check helpers (scoring, dedup)."""

from __future__ import annotations

from pathlib import Path

from bot.reports.market_moving_score import score_market_moving
from bot.reports.telegram_report_dedup import check_duplicate, record_sent


def test_score_stacked_tier1_high() -> None:
    s = score_market_moving(
        "Earnings surprise and SEC investigation; guidance cut — trading halt",
        symbol="NVDA",
    )
    assert s.score >= 70


def test_dedup_skips_same_title(tmp_path: Path) -> None:
    p = tmp_path / "dedup.json"
    d1 = check_duplicate(
        p, "Hello World", "http://a", "AAPL", window_hours=24
    )
    assert d1.is_duplicate is False
    record_sent(p, d1.key)
    d2 = check_duplicate(
        p, "Hello World", "http://a", "AAPL", window_hours=24
    )
    assert d2.is_duplicate is True
