"""Throttle Forex auto-paper Telegram duplicates (TWS down spam)."""

from __future__ import annotations

import json
import time
from pathlib import Path

_REL = "data/runtime/forex_telegram_throttle.json"


def should_send_throttled(
    project_root: Path, *, key: str, min_interval_sec: float = 900.0
) -> bool:
    p = Path(project_root).resolve() / _REL
    now = time.time()
    data: dict[str, float] = {}
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): float(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            data = {}
    last = float(data.get(key, 0.0))
    if now - last < float(min_interval_sec):
        return False
    data[key] = now
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=0) + "\n", encoding="utf-8")
    return True


def send_fx_telegram(
    *,
    project_root: Path,
    cfg,
    journal,
    body: str,
    throttle_key: str | None = None,
) -> bool:
    """Safely send Telegram if configured; optional throttle by key."""

    try:
        from bot.notifications import send_telegram_message
    except Exception:
        return False
    if throttle_key and not should_send_throttled(project_root, key=throttle_key):
        return False
    try:
        return bool(send_telegram_message(body, cfg=cfg, journal=journal))
    except Exception:
        return False


__all__ = ["send_fx_telegram", "should_send_throttled"]
