"""Stable trade row ids for Journal / CLI (read-only identifiers).

Must stay in sync with :func:`bot_ui.services.state_store._row_from_paper_order`
inputs: same blob string used when JSONL rows are enumerated with line numbers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_stable_trade_row_id(
    source_path: str,
    line_no: int,
    obj: dict[str, Any],
) -> str:
    """Return a deterministic, URL-safe id for one JSONL paper-order row."""
    oid0 = ""
    raw_ids = obj.get("order_ids")
    if isinstance(raw_ids, list) and raw_ids:
        oid0 = str(raw_ids[0])
    pe = ""
    try:
        v = obj.get("parent_entry_order_id") or obj.get("entry_order_id")
        if v is not None:
            pe = str(int(v))
    except (TypeError, ValueError):
        pe = str(obj.get("parent_entry_order_id") or obj.get("entry_order_id") or "")
    skipped = sorted(str(s) for s in (obj.get("skipped_reasons") or []) if s)
    parts = (
        source_path.strip(),
        str(line_no),
        str(obj.get("timestamp") or obj.get("ts") or "").strip(),
        str(obj.get("symbol") or "").strip().upper(),
        str(obj.get("direction") or "").strip().lower(),
        str(obj.get("entry") if obj.get("entry") is not None else ""),
        oid0,
        pe,
        ",".join(skipped),
        str(bool(obj.get("submitted"))),
        str(bool(obj.get("submitted_to_broker"))),
        str(obj.get("bracket_integrity") or ""),
    )
    blob = "|".join(parts)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:22]
    return digest
