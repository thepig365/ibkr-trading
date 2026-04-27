"""Optional email delivery for generated reports (Resend, env-based; no secrets in logs)."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


RESEND_API_URL = "https://api.resend.com/emails"
# Resend requires a non-empty User-Agent; missing it yields HTTP 403 error code 1010.
RESEND_USER_AGENT = "StrategyLab/1.0 (+https://local.strategy-lab)"


@dataclass
class SendOutcome:
    status: str  # sent | skipped_missing_credentials | failed | failed_user_agent_or_resend_access_denied
    detail: str = ""


def _resend_key() -> str:
    return (os.environ.get("RESEND_API_KEY") or "").strip()


def _from_addr() -> str:
    return (os.environ.get("REPORT_EMAIL_FROM") or "reports@resend.dev").strip()


def _to_addr(configured: str) -> str:
    env = (os.environ.get("REPORT_EMAIL_TO") or "").strip()
    if env:
        return env
    return (configured or "ileonzh@gmail.com").strip()


def _resend_request_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": RESEND_USER_AGENT,
    }


def _http_error_outcome(exc: urllib.error.HTTPError) -> SendOutcome:
    body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
    if int(exc.code) == 403 and "1010" in body:
        return SendOutcome(
            "failed_user_agent_or_resend_access_denied",
            "Resend rejected the request with 403/1010. This is commonly caused by "
            "missing User-Agent or access policy.",
        )
    return SendOutcome("failed", f"HTTP {exc.code}: {body[:200]}")


def send_report_email(
    *,
    to_cfg: str,
    subject: str,
    text_body: str,
    project_root: Any = None,  # unused; reserved
) -> SendOutcome:
    """Send a plain-text report email via Resend. Never raises for missing creds."""
    key = _resend_key()
    if not key:
        return SendOutcome("skipped_missing_credentials", "RESEND_API_KEY not set")
    to = _to_addr(to_cfg)
    if not to or "@" not in to:
        return SendOutcome("skipped_missing_credentials", "No recipient")
    from_a = _from_addr()
    payload = {
        "from": from_a,
        "to": [to],
        "subject": subject[:900],
        "text": text_body[:100_000],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - fixed URL, controlled JSON
        RESEND_API_URL,
        data=data,
        method="POST",
        headers=_resend_request_headers(key),
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if 200 <= int(resp.status) < 300:
                return SendOutcome("sent", raw[:200])
    except urllib.error.HTTPError as exc:
        return _http_error_outcome(exc)
    except OSError as exc:
        return SendOutcome("failed", str(exc)[:200])
    return SendOutcome("failed", "unknown")
