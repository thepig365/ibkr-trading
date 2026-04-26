"""Optional email delivery for generated reports (Resend, env-based; no secrets in logs)."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class SendOutcome:
    status: str  # sent | skipped_missing_credentials | failed
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
        "https://api.resend.com/emails",
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if 200 <= int(resp.status) < 300:
                return SendOutcome("sent", raw[:200])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return SendOutcome("failed", f"HTTP {exc.code}: {body[:200]}")
    except OSError as exc:
        return SendOutcome("failed", str(exc)[:200])
    return SendOutcome("failed", "unknown")
