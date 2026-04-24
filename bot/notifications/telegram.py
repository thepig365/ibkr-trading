"""Telegram notification adapter.

Design rules
------------
* Missing credentials MUST NEVER crash the bot. We fall back to
  appending the message to ``memory/DAILY-SUMMARY.md``, print a clear
  console warning, and return ``False``.
* Network errors are caught; the function logs and returns ``False``.
* When ``privacy_mode`` is enabled, account numbers, bot tokens, and
  dollar amounts are redacted before the message leaves the process.
* When ``parse_mode == "HTML"``, caller-supplied text is HTML-escaped
  so strategy messages cannot accidentally inject markup.
* If a Journal is provided, every send attempt is recorded in the
  ``telegram_messages`` table for auditability.
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from ..config import AppConfig, load_config

if TYPE_CHECKING:  # pragma: no cover
    from ..journal import Journal

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

Severity = Literal["info", "warning", "urgent"]

_SEVERITY_TAG: dict[str, str] = {
    "info": "[INFO]",
    "warning": "[WARN]",
    "urgent": "[URGENT]",
}

# Redaction patterns applied when privacy_mode is on.
_ACCOUNT_RE = re.compile(r"\bD[UF][A-Z0-9]{3,}\b")  # DU…/DF… paper/live account ids
_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")
_DOLLAR_RE = re.compile(r"\$\s?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?")
_BARE_NUMERIC_LABEL_RE = re.compile(
    r"(?i)\b(net[_ ]?liquidation|total[_ ]?cash|available[_ ]?funds|"
    r"buying[_ ]?power|cash[_ ]?value|realized[_ ]?pnl|unrealized[_ ]?pnl|pnl)"
    r"\s*[:=]\s*-?\d[\d,]*(?:\.\d+)?"
)
_API_KEY_RE = re.compile(r"(?i)\b(api[_ ]?key|token|secret)\s*[:=]\s*\S+")


def _append_daily_summary_fallback(cfg: AppConfig, text: str, reason: str) -> None:
    path = cfg.absolute(cfg.settings.paths.daily_summary_md)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block = f"\n## {ts} (telegram fallback: {reason})\n\n{text}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def _redact(text: str) -> str:
    """Best-effort privacy redaction for outbound Telegram text.

    We keep the first two characters of account numbers so operators can
    still tell paper (DU) from live (DF) accounts.
    """

    def _acct_sub(m: re.Match[str]) -> str:
        s = m.group(0)
        return s[:2] + "***" + s[-2:] if len(s) > 4 else "D****"

    out = _ACCOUNT_RE.sub(_acct_sub, text)
    out = _BOT_TOKEN_RE.sub("***", out)
    out = _API_KEY_RE.sub(r"\1=***", out)
    out = _BARE_NUMERIC_LABEL_RE.sub(lambda m: f"{m.group(1)}=***", out)
    out = _DOLLAR_RE.sub("$***", out)
    return out


def _build_payload(
    cfg: AppConfig,
    text: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "chat_id": cfg.telegram.chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    parse_mode = cfg.settings.notifications.telegram.parse_mode
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return payload


def _console_warn(message: str) -> None:
    """Write a visible warning to stderr even when logging is filtered."""
    # Use print so it remains visible regardless of the logging config.
    print(f"[telegram] {message}", flush=True)
    logger.warning(message)


def send_telegram_message(
    text: str,
    cfg: AppConfig | None = None,
    journal: "Journal | None" = None,
    timeout: float = 10.0,
) -> bool:
    """Send a Telegram message.

    Returns ``True`` only if Telegram acknowledged delivery. Returns
    ``False`` on missing credentials, disabled notifications, or any
    network / API error. Never raises.
    """
    cfg = cfg or load_config()

    if cfg.settings.notifications.telegram.privacy_mode:
        text = _redact(text)

    tele = cfg.settings.notifications.telegram

    if not tele.enabled:
        reason = "telegram disabled in settings"
        _append_daily_summary_fallback(cfg, text, reason)
        if journal is not None:
            journal.record_telegram_message(
                cfg.telegram.chat_id, text, delivered=False, error="disabled"
            )
        return False

    if not cfg.telegram.is_configured:
        _console_warn(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing; "
            "writing message to memory/DAILY-SUMMARY.md instead."
        )
        _append_daily_summary_fallback(
            cfg, text, "missing TELEGRAM_BOT_TOKEN/CHAT_ID"
        )
        if journal is not None:
            journal.record_telegram_message(
                cfg.telegram.chat_id,
                text,
                delivered=False,
                error="missing_credentials",
            )
        return False

    url = TELEGRAM_API_URL.format(token=cfg.telegram.bot_token)
    payload = _build_payload(cfg, text)
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - we never want this to crash
        logger.warning("Telegram send failed: %s", exc)
        _append_daily_summary_fallback(cfg, text, f"network error: {exc!r}")
        if journal is not None:
            journal.record_telegram_message(
                cfg.telegram.chat_id, text, delivered=False, error=str(exc)
            )
        return False

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}

    delivered = resp.status_code == 200 and bool(body.get("ok", False))
    if not delivered:
        logger.warning(
            "Telegram API rejected message: status=%s body=%s",
            resp.status_code,
            resp.text,
        )
        _append_daily_summary_fallback(
            cfg, text, f"API rejection status={resp.status_code}"
        )

    if journal is not None:
        journal.record_telegram_message(
            cfg.telegram.chat_id,
            text,
            delivered=delivered,
            error=None if delivered else f"status={resp.status_code}",
        )
    return delivered


# ----------------------------------------------------------------------
# High-level helper
# ----------------------------------------------------------------------
def _format_event_body(
    cfg: AppConfig,
    event_type: str,
    title: str,
    body: str,
    severity: Severity,
) -> str:
    tag = _SEVERITY_TAG.get(severity, "[INFO]")
    parse_mode = cfg.settings.notifications.telegram.parse_mode

    if parse_mode == "HTML":
        safe_title = html.escape(title)
        safe_body = html.escape(body)
        safe_event = html.escape(event_type)
        return (
            f"<b>{html.escape(tag)} {safe_title}</b>\n"
            f"<i>{safe_event}</i>\n\n"
            f"{safe_body}"
        )
    # Plain text (no parse_mode) or Markdown: keep it simple and avoid
    # escaping rules that differ between MarkdownV1/V2.
    return f"{tag} {title}\n{event_type}\n\n{body}"


def notify_event(
    event_type: str,
    title: str,
    body: str,
    severity: Severity = "info",
    cfg: AppConfig | None = None,
    journal: "Journal | None" = None,
) -> bool:
    """Structured notification helper.

    ``event_type``  A short machine-readable identifier, e.g.
                    ``reconciliation.failed``. Not redacted.
    ``title``       Human-readable headline. HTML-escaped when the
                    configured parse_mode is HTML.
    ``body``        Multi-line details. HTML-escaped when the configured
                    parse_mode is HTML; privacy redactor still applies.
    ``severity``    One of ``info``, ``warning``, ``urgent``. Maps to a
                    visible text prefix so the operator can triage at a
                    glance.

    Returns ``True`` when Telegram acknowledged delivery, ``False``
    otherwise (fallback to DAILY-SUMMARY.md will have been written).
    """
    cfg = cfg or load_config()
    if severity not in _SEVERITY_TAG:
        raise ValueError(
            f"severity must be one of {sorted(_SEVERITY_TAG)!r}, got {severity!r}"
        )
    text = _format_event_body(cfg, event_type, title, body, severity)
    return send_telegram_message(text, cfg=cfg, journal=journal)


__all__ = ["send_telegram_message", "notify_event"]


# Helpful accessor for tests that want to poke env-derived credentials
# without going through AppConfig.
def _env_credentials() -> tuple[str | None, str | None]:  # pragma: no cover
    return (
        os.getenv("TELEGRAM_BOT_TOKEN") or None,
        os.getenv("TELEGRAM_CHAT_ID") or None,
    )


# Preserve a typed re-export path for callers that import the fallback
# helper directly from tests.
def _fallback_path(cfg: AppConfig) -> Path:  # pragma: no cover - trivial
    return cfg.absolute(cfg.settings.paths.daily_summary_md)
