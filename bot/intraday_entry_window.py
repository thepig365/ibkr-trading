"""Intraday paper new-entry window in an operator wall-clock timezone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import IntradayPaperConfig


def parse_hhmm(s: str) -> tuple[int, int]:
    raw = (s or "").strip()
    lp = raw.split(":", 1)
    try:
        h = int(lp[0].strip())
        mi = int((lp[1] if len(lp) > 1 else "0").strip())
    except (ValueError, IndexError):
        raise ValueError(f"invalid HH:MM time: {s!r}") from None
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"hour/minute out of range: {s!r}")
    return h, mi


def minute_of_day(h: int, mi: int) -> int:
    return h * 60 + mi


def intraday_entries_overnight(*, open_m: int, close_m: int) -> bool:
    """True when inclusive window crosses local midnight (e.g. 22:30–01:30)."""

    return open_m > close_m


def tz_weekday_new_entries_allow(
    tz_name: str,
    *,
    start_hhmm: tuple[int, int],
    end_hhmm: tuple[int, int],
    now_local: datetime | None = None,
) -> tuple[bool, str]:
    """Weekdays Mon–Fri in *tz_name*. Optional *now_local* (naive or aware) overrides 'now'."""

    zn = str(tz_name or "").strip() or "America/New_York"
    try:
        z = ZoneInfo(zn)
    except Exception:
        return False, f"invalid timezone for entry window: {zn!r}"

    try:
        if now_local is None:
            at = datetime.now(z)
        elif now_local.tzinfo is None:
            at = now_local.replace(tzinfo=z)
        else:
            at = now_local.astimezone(z)
    except Exception:
        at = datetime.now(z)

    w = int(at.weekday())
    m = at.hour * 60 + at.minute
    sh = minute_of_day(*start_hhmm)
    eh = minute_of_day(*end_hhmm)
    rng = (
        f"weekday-only {start_hhmm[0]:02d}:{start_hhmm[1]:02d}-"
        f"{end_hhmm[0]:02d}:{end_hhmm[1]:02d} ({zn})"
    )

    if sh <= eh:
        if w >= 5:
            return False, f"outside {rng} — weekend ({zn})"
        if sh <= m <= eh:
            return True, ""
        return False, f"outside {rng} — now={at.strftime('%H:%M')} ({zn})"

    if m >= sh:
        if w >= 5:
            return False, f"outside {rng} — weekend evening ({zn})"
        return True, ""
    if m <= eh:
        if w >= 5:
            return False, f"outside {rng} — weekend after midnight ({zn})"
        return True, ""

    if eh < m < sh and w < 5:
        return False, f"outside {rng} — between sessions ({zn})"

    return False, f"outside {rng}"


def intraday_session_label(ip: IntradayPaperConfig) -> str:
    z = str(ip.new_entries_wall_clock_timezone or "America/New_York").strip()
    return f"{ip.no_new_entries_before}–{ip.no_new_entries_after} {z}"


def intraday_new_entries_allow_config(
    ip: IntradayPaperConfig,
    *,
    now_local: datetime | None = None,
) -> tuple[bool, str]:
    st = parse_hhmm(ip.no_new_entries_before)
    en = parse_hhmm(ip.no_new_entries_after)
    return tz_weekday_new_entries_allow(
        ip.new_entries_wall_clock_timezone,
        start_hhmm=st,
        end_hhmm=en,
        now_local=now_local,
    )


def entry_timezone_now_display(ip: IntradayPaperConfig) -> tuple[str, str, int, int]:
    """Return (ISO local, HH:MM local, weekday 0..6, minute-of-day local) in entry TZ."""

    zn = str(ip.new_entries_wall_clock_timezone or "America/New_York").strip()
    z = ZoneInfo(zn)
    at = datetime.now(z)
    now_l = at.astimezone(z)
    w = int(now_l.weekday())
    m = now_l.hour * 60 + now_l.minute
    return now_l.isoformat(), now_l.strftime("%H:%M"), w, m


def between_overnight_sessions_weekday_quiet(
    ip: IntradayPaperConfig,
    *,
    weekday: int,
    minute: int,
) -> bool:
    """After local session tail (e.g. past 01:30) and before evening open (22:30), Mon–Fri."""

    if weekday >= 5:
        return False
    sh = minute_of_day(*parse_hhmm(ip.no_new_entries_before))
    eh = minute_of_day(*parse_hhmm(ip.no_new_entries_after))
    if sh <= eh:
        return False
    return eh < minute < sh


def ny_premarket_compatible(
    ip: IntradayPaperConfig,
    *,
    ny_weekday: int,
    ny_minute: int,
) -> bool:
    """True when classic 08:30–09:44 NY pre-RTH band matches this config (same-day NY slice)."""

    if str(ip.new_entries_wall_clock_timezone or "").strip() != "America/New_York":
        return False
    sh = minute_of_day(*parse_hhmm(ip.no_new_entries_before))
    eh = minute_of_day(*parse_hhmm(ip.no_new_entries_after))
    if sh > eh:
        return False
    if ny_weekday >= 5:
        return False
    return (8 * 60 + 30) <= ny_minute < sh


__all__ = [
    "between_overnight_sessions_weekday_quiet",
    "entry_timezone_now_display",
    "intraday_entries_overnight",
    "intraday_new_entries_allow_config",
    "intraday_session_label",
    "minute_of_day",
    "ny_premarket_compatible",
    "parse_hhmm",
    "tz_weekday_new_entries_allow",
]
