"""journal skip reason mapping (presentation only — no execution)."""

from __future__ import annotations

from bot.ux.humanize import humanize_skip_reason, humanize_skip_reasons


def test_humanize_short_disabled() -> None:
    raw = (
        "trading_allow_shorting=false; cannot submit short bracket for PLTR"
    )
    assert "short selling is disabled" in humanize_skip_reason(raw).lower()


def test_humanize_open_order_duplicate() -> None:
    raw = "open order exists for PLTR — refuse duplicate paper entry"
    h = humanize_skip_reason(raw)
    assert "open order" in h.lower()


def test_humanize_position_duplicate() -> None:
    raw = "existing position in AVGO — refuse duplicate paper entry"
    h = humanize_skip_reason(raw)
    assert "position" in h.lower()


def test_humanize_list_wraps_each() -> None:
    xs = humanize_skip_reasons(["waiting_for_1m_trigger", "kill switch active"])
    assert len(xs) == 2
    assert "kill" in xs[1].lower()


def test_humanize_bracket_incomplete_zh() -> None:
    assert "括号" in humanize_skip_reason("bracket incomplete", locale="zh")


def test_humanize_short_zh() -> None:
    raw = (
        "trading_allow_shorting=false; cannot submit short bracket for PLTR"
    )
    h = humanize_skip_reason(raw, locale="zh")
    assert "做空" in h or "跳过" in h


def test_humanize_short_en() -> None:
    raw = (
        "trading_allow_shorting=false; cannot submit short bracket for PLTR"
    )
    h = humanize_skip_reason(raw, locale="en")
    assert "short" in h.lower()


def test_humanize_skip_reasons_passes_locale() -> None:
    xs = humanize_skip_reasons(
        ["bracket incomplete"],
        locale="zh",
    )
    assert xs and ("括号" in xs[0] or "不完整" in xs[0])
