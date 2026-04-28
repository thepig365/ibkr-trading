"""Locate paper-order JSON rows by stable trade id (local files only, no broker)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .journal_trade_id import compute_stable_trade_row_id


def iter_intraday_paper_order_jsonl_files(paper_orders_dir: Path) -> list[Path]:
    if not paper_orders_dir.is_dir():
        return []
    return sorted(paper_orders_dir.glob("*-intraday-paper-orders.jsonl"))


def find_paper_order_payload_by_trade_id(
    project_root: Path,
    trade_id: str,
) -> dict[str, Any] | None:
    """Return the raw dict for *trade_id* or None when not found."""
    want = (trade_id or "").strip().lower()
    if len(want) < 16:
        return None
    pod = (project_root / "data" / "paper_orders").resolve()
    for path in iter_intraday_paper_order_jsonl_files(pod):
        sp = str(path)
        try:
            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    rid = compute_stable_trade_row_id(sp, line_no, obj).lower()
                    if rid == want:
                        return obj
        except OSError:
            continue
    return None
