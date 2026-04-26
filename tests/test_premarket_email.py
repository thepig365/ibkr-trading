"""Pre-market email body includes safety reminder; missing key skips."""

from __future__ import annotations

import os
from unittest.mock import patch

from bot.premarket.brief import PremarketBriefData, _email_body
from bot.reports.report_email import send_report_email


def test_email_body_contains_ict_trigger_reminder() -> None:
    d = PremarketBriefData(
        date_ny="2026-04-25",
        generated_at_utc="2026-04-25T12:00:00Z",
        market_tone="Calm",
        summary_lines=["x"],
        macro_events=[],
        headlines=[{"title": "t", "symbol": ""}],
        provider_status={},
        watchlist_symbols=["NVDA"],
        risk_flags=[],
    )
    body = _email_body(d)
    assert "ICT" in body or "1-minute" in body or "trigger" in body.lower()


def test_send_premarket_email_skips_without_resend() -> None:
    with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
        out = send_report_email(
            to_cfg="ileonzh@gmail.com",
            subject="Strategy Lab Pre-Market Brief — 2026-04-25",
            text_body="x",
        )
    assert out.status == "skipped_missing_credentials"
