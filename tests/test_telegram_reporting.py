"""Telegram / report formatters: concise, no secrets in strings."""

from __future__ import annotations

from pathlib import Path

from bot.config import load_config
from bot.reports.telegram_formatters import format_market_moving_telegram, ny_session_label

REPO = Path(__file__).resolve().parent.parent


def test_format_market_moving_telegram_not_json() -> None:
    s = format_market_moving_telegram(
        title="SEC opens probe into X",
        tickers="ABC",
        why_matters="regulatory, keywords matched",
        score=88,
        session_label="10:00 NY",
    )
    assert "{" not in s
    assert "SEC" in s or "probe" in s
    assert "not" in s.lower() and "trigger" in s.lower()


def test_ny_session_label_includes_ny() -> None:
    cfg = load_config(project_root=REPO)
    label = ny_session_label(cfg)
    assert "NY" in label
