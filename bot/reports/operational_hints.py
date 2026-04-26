"""Non-secret UI hints from last read-only broker CLI (files under data/runtime/)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HINTS_RELPATH = "data/runtime/operational_hints.json"


@dataclass
class OperationalHints:
    open_orders_n: int | None = None
    reconcile_pass: bool | None = None
    account_mode: str = "paper"
    updated_utc: str = ""
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.updated_utc and self.open_orders_n is None and self.reconcile_pass is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "open_orders_n": self.open_orders_n,
            "reconcile_pass": self.reconcile_pass,
            "account_mode": self.account_mode,
            "updated_utc": self.updated_utc,
            "note": self.note,
        }


def _path(root: Path) -> Path:
    p = (root / HINTS_RELPATH).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_operational_hints(project_root: Path) -> OperationalHints:
    p = _path(project_root)
    if not p.is_file():
        return OperationalHints(
            account_mode=(os.environ.get("IBKR_ACCOUNT_MODE") or "paper").lower(),
            note="Run Open Orders / Paper Reconcile to refresh.",
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return OperationalHints(note="(could not read hints file)")
    if not isinstance(data, dict):
        return OperationalHints()
    return OperationalHints(
        open_orders_n=data.get("open_orders_n"),
        reconcile_pass=data.get("reconcile_pass"),
        account_mode=str(data.get("account_mode") or "paper"),
        updated_utc=str(data.get("updated_utc") or ""),
        note=str(data.get("note") or ""),
    )


def _merge_write(project_root: Path, **updates: Any) -> None:
    cur = load_operational_hints(project_root)
    d = cur.as_dict()
    for k, v in updates.items():
        if v is not None:
            d[k] = v
    d["updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d["account_mode"] = (os.environ.get("IBKR_ACCOUNT_MODE") or "paper").lower()
    p = _path(project_root)
    p.write_text(
        json.dumps(d, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_open_orders_count(project_root: Path, n: int) -> None:
    _merge_write(project_root, open_orders_n=int(n), note="")


def write_reconcile_status(project_root: Path, passed: bool) -> None:
    _merge_write(project_root, reconcile_pass=bool(passed), note="")
