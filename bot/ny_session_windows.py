"""US cash session time windows (America/New_York). No broker / IBKR imports."""

from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo


def us_ny_rth_window_allows(
    open_hhmm: tuple[int, int],
    close_hhmm: tuple[int, int],
) -> tuple[bool, str]:
    """True when *now* (America/New_York) is a weekday within [open, close] inclusive by minute."""
    z = ZoneInfo("America/New_York")
    now = datetime.now(z)
    if now.weekday() >= 5:
        return False, "weekend (US)"
    minutes = now.hour * 60 + now.minute
    o = open_hhmm[0] * 60 + open_hhmm[1]
    c = close_hhmm[0] * 60 + close_hhmm[1]
    if minutes < o or minutes > c:
        return False, (
            f"outside {open_hhmm[0]:02d}:{open_hhmm[1]:02d}-"
            f"{close_hhmm[0]:02d}:{close_hhmm[1]:02d} America/New_York"
        )
    return True, ""


def us_rth_allows_new_entries(
    *,
    open_hhmm: tuple[int, int] = (9, 45),
    close_hhmm: tuple[int, int] = (15, 30),
) -> tuple[bool, str]:
    """Default US cash RTH new-entry window for intraday paper (configurable bounds)."""
    return us_ny_rth_window_allows(open_hhmm, close_hhmm)


def us_morning_paper_window_allows() -> tuple[bool, str]:
    """Morning forward-test window (NY): 09:45–11:30, weekdays.

    Used when ``run-auto-paper-intraday-loop --session morning`` (future smoke test).
    """
    return us_ny_rth_window_allows((9, 45), (11, 30))


__all__ = [
    "us_morning_paper_window_allows",
    "us_ny_rth_window_allows",
    "us_rth_allows_new_entries",
]
