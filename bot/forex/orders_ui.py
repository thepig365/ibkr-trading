"""Read-only helpers for Forex order JSONL — UI tables (no broker)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import FOREX_ORDERS_DIR
from .pairs import parse_pair


def _orders_dir(project_root: Path) -> Path:
    return Path(project_root).resolve() / FOREX_ORDERS_DIR


def iter_forex_order_events(
    project_root: Path,
    *,
    max_files: int = 21,
    max_lines_per_file: int = 500,
) -> list[dict[str, Any]]:
    """Newest JSONL files first; lines oldest-first within each file."""

    od = _orders_dir(project_root)
    if not od.is_dir():
        return []
    paths = sorted(od.glob("*-forex-paper-orders.jsonl"), reverse=True)[:max_files]
    out: list[dict[str, Any]] = []
    for p in reversed(paths):  # chronological: older files then newer
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines()[-max_lines_per_file:]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row["_source_file"] = p.name
                out.append(row)
    return out


def _slug_from_record(rec: dict[str, Any]) -> str | None:
    pair = rec.get("pair")
    if not pair:
        return None
    try:
        return parse_pair(str(pair)).slug.upper()
    except ValueError:
        return None


def summarize_row_for_ui(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested broker payloads for templates."""

    broker = rec.get("broker") if isinstance(rec.get("broker"), dict) else {}
    statuses = broker.get("statuses") or rec.get("child_status_snapshots") or []
    status_first = statuses[0] if isinstance(statuses, list) and statuses else {}

    rej = broker.get("error") or rec.get("message") or status_first.get("status")
    ok_submit = broker.get("ok") if isinstance(broker.get("ok"), bool) else None

    return {
        "ts_utc": rec.get("ts_utc"),
        "phase": rec.get("phase"),
        "pair": rec.get("pair"),
        "pair_slug": _slug_from_record(rec),
        "direction": rec.get("direction"),
        "notional_usd_est": rec.get("notional_usd_est"),
        "entry": rec.get("entry"),
        "stop": rec.get("stop"),
        "target": rec.get("target"),
        "units": rec.get("units"),
        "local_state": rec.get("status") or rec.get("phase"),
        "broker_acceptance": ok_submit,
        "fill_hint": status_first.get("status") if status_first else None,
        "filled_qty": status_first.get("filled"),
        "reject_reason": rej,
        "_raw": rec,
    }


__all__ = ["iter_forex_order_events", "summarize_row_for_ui"]
