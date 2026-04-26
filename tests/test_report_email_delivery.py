"""Report email: missing credentials, no secret leakage in outcomes."""

from __future__ import annotations

import os
from unittest.mock import patch

from bot.reports.report_email import send_report_email


def test_send_report_email_skips_without_resend_key() -> None:
    with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
        out = send_report_email(
            to_cfg="ileonzh@gmail.com",
            subject="t",
            text_body="hello",
        )
    assert out.status == "skipped_missing_credentials"
    assert "RESEND" in (out.detail or "")


def test_send_report_email_does_not_put_secret_in_outcome_detail() -> None:
    fake_key = "re_00000000deadbeefcafe"  # noqa: S105 - test placeholder only
    with patch.dict(
        os.environ,
        {
            "RESEND_API_KEY": fake_key,
            "REPORT_EMAIL_FROM": "onboarding@resend.dev",
        },
    ):
        with patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = b'{"id":"1"}'
            uo.return_value.__enter__.return_value.status = 200
            out = send_report_email(
                to_cfg="ileonzh@gmail.com",
                subject="Daily",
                text_body="metrics: 1",
            )
    assert out.status == "sent"
    assert fake_key not in (out.detail or "")
