"""Deduplication for Telegram report lines (headlines/URLs). No secrets in file."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATUS_SENT = "sent"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_SKIPPED_NOT_MARKET_MOVING = "skipped_not_market_moving"
STATUS_SKIPPED_MISSING_CREDENTIALS = "skipped_missing_credentials"
STATUS_FAILED_DELIVERY = "failed_delivery"
STATUS_FAILED_PROVIDER = "failed_provider"


def _norm_key(title: str, url: str, symbol: str) -> str:
    raw = f"{(title or '').strip().lower()}|{(url or '').strip()}|{(symbol or '').strip().upper()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class DedupCheck:
    is_duplicate: bool
    key: str


def load_dedup_map(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    m = raw.get("keys")
    return dict(m) if isinstance(m, dict) else {}


def save_dedup_map(path: Path, m: dict[str, str], *, max_entries: int = 2000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # keep most recent by value (iso time) if too large
    if len(m) > max_entries:
        items = sorted(m.items(), key=lambda kv: kv[1], reverse=True)[:max_entries]
        m = dict(items)
    path.write_text(
        json.dumps({"keys": m, "updated_utc": _now_iso()}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prune_old(
    m: dict[str, str], *, window_hours: int, now: datetime | None = None
) -> dict[str, str]:
    if window_hours <= 0:
        return m
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    out: dict[str, str] = {}
    for k, v in m.items():
        try:
            ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff.replace(tzinfo=timezone.utc):
                out[k] = v
        except (TypeError, ValueError):
            continue
    return out


def check_duplicate(
    path: Path,
    title: str,
    url: str,
    symbol: str,
    *,
    window_hours: int,
) -> DedupCheck:
    key = _norm_key(title, url, symbol)
    m = prune_old(load_dedup_map(path), window_hours=window_hours)
    is_dup = key in m
    return DedupCheck(is_duplicate=is_dup, key=key)


def record_sent(path: Path, key: str) -> None:
    m = load_dedup_map(path)
    m[key] = _now_iso()
    save_dedup_map(path, m)


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {**payload, "updated_utc": _now_iso()}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return o if isinstance(o, dict) else {}


__all__ = [
    "DedupCheck",
    "check_duplicate",
    "load_dedup_map",
    "prune_old",
    "read_state",
    "record_sent",
    "write_state",
    "STATUS_FAILED_DELIVERY",
    "STATUS_FAILED_PROVIDER",
    "STATUS_SENT",
    "STATUS_SKIPPED_DUPLICATE",
    "STATUS_SKIPPED_MISSING_CREDENTIALS",
    "STATUS_SKIPPED_NOT_MARKET_MOVING",
]
