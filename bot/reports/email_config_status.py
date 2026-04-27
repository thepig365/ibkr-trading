"""Resend email readiness (no network, no secrets in output dict)."""

from __future__ import annotations

import os
from typing import Any

from ..config import AppConfig


def build_email_config_status(cfg: AppConfig) -> dict[str, Any]:
    """Return booleans + missing field names for Resend delivery readiness.

    ``email_resend_configured`` is true when API key, From, and a recipient
    exist. Recipient may come from ``REPORT_EMAIL_TO`` or ``reports.email_to``
    in settings (config default e.g. ileonzh@gmail.com counts as configured).
    """
    resend = bool((os.environ.get("RESEND_API_KEY") or "").strip())
    rfrom = bool((os.environ.get("REPORT_EMAIL_FROM") or "").strip())
    env_to = bool((os.environ.get("REPORT_EMAIL_TO") or "").strip())
    cfg_to = (cfg.settings.reports.email_to or "").strip()
    reports_email_to_configured = bool(cfg_to and "@" in cfg_to)
    recipient = env_to or reports_email_to_configured

    missing: list[str] = []
    if not resend:
        missing.append("RESEND_API_KEY")
    if not rfrom:
        missing.append("REPORT_EMAIL_FROM")
    if not recipient:
        missing.append("recipient (REPORT_EMAIL_TO or config reports.email_to)")

    email_resend_configured = bool(resend and rfrom and recipient)

    from_raw = (os.environ.get("REPORT_EMAIL_FROM") or "").strip().lower()
    gmail_warn = "@gmail.com" in from_raw if from_raw else False

    return {
        "resend_api_key_present": resend,
        "report_email_from_present": rfrom,
        "report_email_to_present": env_to,
        "reports_email_to_configured": reports_email_to_configured,
        "email_resend_configured": email_resend_configured,
        "missing_fields": missing,
        "from_address_may_need_resend_verification": gmail_warn,
    }
