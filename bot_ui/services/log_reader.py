"""Log/file tail helpers with conservative secret masking.

The Logs page in the UI may show contents of files under the project
data/ and logs/ directories. To make sure no token leaks into a
screenshot, this module replaces anything that *looks* like a Telegram
bot token, Bearer / API key, or DATABASE_URL connection string with
``***REDACTED***`` before the text reaches the browser.

This is best-effort. The right place to keep secrets out of files is at
the source. This is the second line of defence.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Telegram bot token: digits:letters/dashes/underscores (35+ chars)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b")
# Bearer / Authorization header values
_BEARER_RE = re.compile(r"(?i)\b(bearer|token|authorization)[=:\s]+[A-Za-z0-9._\-]{16,}")
# OpenAI / Perplexity-style "sk-" or "pk-" keys
_API_KEY_RE = re.compile(r"\b(?:sk|pk|pcsk)-[A-Za-z0-9]{16,}\b")
# Generic API_KEY=value, *_TOKEN=value, *_SECRET=value
_KV_SECRET_RE = re.compile(
    r"(?im)^(?P<k>[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PWD|DSN))\s*=\s*\S+"
)
# Postgres / supabase URL with credentials
_DB_URL_RE = re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb)\+?[a-z]*://[^\s\"']+")
# AWS-style access keys
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{12,}\b")


def mask_secrets(text: str) -> str:
    if not text:
        return text
    out = _TELEGRAM_TOKEN_RE.sub("***REDACTED-TG-TOKEN***", text)
    out = _BEARER_RE.sub("***REDACTED-AUTH***", out)
    out = _API_KEY_RE.sub("***REDACTED-API-KEY***", out)
    out = _DB_URL_RE.sub("***REDACTED-DB-URL***", out)
    out = _AWS_KEY_RE.sub("***REDACTED-AWS-KEY***", out)
    out = _KV_SECRET_RE.sub(lambda m: f"{m.group('k')}=***REDACTED***", out)
    return out


def safe_relative(path: Path, project_root: Path) -> str:
    """Return a project-relative path string, or '<outside project>' if not."""
    try:
        return str(Path(path).resolve().relative_to(Path(project_root).resolve()))
    except ValueError:
        return "<outside project>"


def is_inside(path: Path, project_root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(project_root).resolve())
    except ValueError:
        return False
    return True


__all__ = ["mask_secrets", "safe_relative", "is_inside"]
