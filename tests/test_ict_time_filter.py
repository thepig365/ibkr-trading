"""ICT strategy time-window filter tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytz

from backend.config import load_config
from backend.strategy.ict_strategy import ICTStrategy

NY = pytz.timezone("America/New_York")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.example.yaml"


def _make_strategy() -> ICTStrategy:
    cfg = load_config(CONFIG_PATH)
    return ICTStrategy(cfg)


def test_silver_bullet_window_active() -> None:
    s = _make_strategy()
    t = NY.localize(datetime(2026, 4, 29, 10, 30))
    assert s.is_active_time(t) is True


def test_dead_zone_blocks_trading() -> None:
    s = _make_strategy()
    t = NY.localize(datetime(2026, 4, 29, 12, 15))
    assert s.is_active_time(t) is False


def test_pm_silver_bullet_active() -> None:
    s = _make_strategy()
    t = NY.localize(datetime(2026, 4, 29, 14, 30))
    assert s.is_active_time(t) is True


def test_after_hours_inactive() -> None:
    s = _make_strategy()
    t = NY.localize(datetime(2026, 4, 29, 17, 0))
    assert s.is_active_time(t) is False
