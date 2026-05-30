"""Wall-clock intraday entry windows (timezone + optional overnight wrap)."""

from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo

from bot.intraday_entry_window import (
    between_overnight_sessions_weekday_quiet,
    intraday_new_entries_allow_config,
    tz_weekday_new_entries_allow,
)
from bot.config import IntradayPaperConfig


def test_melbourne_overnight_inside_evening_tail() -> None:
    z = ZoneInfo("Australia/Melbourne")
    wed_23 = datetime(2026, 5, 6, 23, 0, tzinfo=z)
    ok, _msg = tz_weekday_new_entries_allow(
        "Australia/Melbourne",
        start_hhmm=(22, 30),
        end_hhmm=(1, 30),
        now_local=wed_23,
    )
    assert ok is True


def test_melbourne_overnight_inside_after_midnight() -> None:
    z = ZoneInfo("Australia/Melbourne")
    wed_01 = datetime(2026, 5, 6, 1, 15, tzinfo=z)
    ok, _msg = tz_weekday_new_entries_allow(
        "Australia/Melbourne",
        start_hhmm=(22, 30),
        end_hhmm=(1, 30),
        now_local=wed_01,
    )
    assert ok is True


def test_melbourne_overnight_between_sessions_weekday() -> None:
    z = ZoneInfo("Australia/Melbourne")
    wed_noon = datetime(2026, 5, 6, 12, 0, tzinfo=z)
    ok, msg = tz_weekday_new_entries_allow(
        "Australia/Melbourne",
        start_hhmm=(22, 30),
        end_hhmm=(1, 30),
        now_local=wed_noon,
    )
    assert ok is False
    assert "between sessions" in msg


def test_between_overnight_quiet_helper() -> None:
    ip = IntradayPaperConfig(
        enabled=True,
        new_entries_wall_clock_timezone="Australia/Melbourne",
        no_new_entries_before="22:30",
        no_new_entries_after="01:30",
    )
    assert between_overnight_sessions_weekday_quiet(ip, weekday=2, minute=12 * 60) is True


def test_config_wrapper_matches_tz_allow() -> None:
    ip = IntradayPaperConfig(
        enabled=True,
        new_entries_wall_clock_timezone="Australia/Melbourne",
        no_new_entries_before="22:30",
        no_new_entries_after="01:30",
    )
    z = ZoneInfo("Australia/Melbourne")
    wed_23 = datetime(2026, 5, 6, 23, 0, tzinfo=z)
    ok, _ = intraday_new_entries_allow_config(ip, now_local=wed_23)
    assert ok is True
