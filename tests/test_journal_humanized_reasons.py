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
