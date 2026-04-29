"""Append-only forex paper order audit (JSON lines)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import FOREX_ORDERS_DIR


def forex_orders_path(project_root: Path) -> Path:
    d = datetime.now(timezone.utc).date().isoformat()
    root = Path(project_root).resolve() / FOREX_ORDERS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{d}-forex-paper-orders.jsonl"


def append_forex_order_event(project_root: Path, record: dict[str, Any]) -> Path:
    p = forex_orders_path(project_root)
    rec = dict(record)
    rec.setdefault("ts_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return p


__all__ = ["append_forex_order_event", "forex_orders_path"]
