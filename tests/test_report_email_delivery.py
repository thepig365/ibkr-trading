"""Report email: Resend headers, outcomes, no secret leakage."""

from __future__ import annotations

import os
import urllib.error
from io import BytesIO
from unittest.mock import patch

from bot.reports.report_email import (
    RESEND_USER_AGENT,
    send_report_email,
)


def test_send_report_email_skips_without_resend_key() -> None:
    with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
        out = send_report_email(
            to_cfg="ileonzh@gmail.com",
            subject="t",
            text_body="hello",
        )
    assert out.status == "skipped_missing_credentials"
    assert "RESEND" in (out.detail or "")


def test_send_report_email_includes_user_agent_header() -> None:
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
            send_report_email(
                to_cfg="ileonzh@gmail.com",
                subject="Daily",
                text_body="metrics: 1",
            )
    assert uo.called
    req = uo.call_args[0][0]
    headers = dict(req.header_items())
    ua = headers.get("User-agent") or headers.get("User-Agent") or ""
    assert ua == RESEND_USER_AGENT
    assert headers.get("Authorization", "").startswith("Bearer ")


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


def test_resend_403_1010_maps_to_user_agent_status() -> None:
    body_json = b'{"statusCode":403,"name":"forbidden","message":"error code: 1010"}'
    err = urllib.error.HTTPError(
        "https://api.resend.com/emails",
        403,
        "Forbidden",
        hdrs={},
        fp=BytesIO(body_json),
    )

    fake_key = "re_00000000deadbeefcafe"  # noqa: S105
    with patch.dict(
        os.environ,
        {
            "RESEND_API_KEY": fake_key,
            "REPORT_EMAIL_FROM": "onboarding@resend.dev",
        },
    ):
        with patch("urllib.request.urlopen", side_effect=err) as uo:  # noqa: ARG005
            out = send_report_email(
                to_cfg="ileonzh@gmail.com",
                subject="t",
                text_body="x",
            )
    assert out.status == "failed_user_agent_or_resend_access_denied"
    assert "403/1010" in (out.detail or "")
    assert "User-Agent" in (out.detail or "") or "403/1010" in (out.detail or "")
    assert fake_key not in (out.detail or "")


def test_resend_other_http_error_stays_failed() -> None:
    body_json = b'{"statusCode":401,"message":"Invalid API key"}'
    err = urllib.error.HTTPError(
        "https://api.resend.com/emails",
        401,
        "Unauthorized",
        hdrs={},
        fp=BytesIO(body_json),
    )
    with patch.dict(
        os.environ,
        {
            "RESEND_API_KEY": "re_test_placeholder",
            "REPORT_EMAIL_FROM": "onboarding@resend.dev",
        },
    ):
        with patch("urllib.request.urlopen", side_effect=err):
            out = send_report_email(
                to_cfg="ileonzh@gmail.com",
                subject="t",
                text_body="x",
            )
    assert out.status == "failed"
    assert "401" in (out.detail or "")
