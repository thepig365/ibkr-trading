"""Chart overlay prefers reconciled entry/exit in payload merge."""

from __future__ import annotations

import json
from pathlib import Path

from bot.fills_reconciliation import merge_reconciliation_into_trade_payload


def test_merge_overlay_entry_exit(tmp_path: Path) -> None:
    lst = tmp_path / "data" / "runtime" / "fills_reconciliation_last.json"
    lst.parent.mkdir(parents=True, exist_ok=True)
    tid = "b" * 22
    lst.write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "trade_id": tid,
                        "status": "closed",
                        "entry_fill_price": 100.25,
                        "entry_fill_time": "2026-04-05T13:05:06+00:00",
                        "exit_fill_price": 102.10,
                        "exit_fill_time": "2026-04-05T14:06:06+00:00",
                    },
                ]
            },
        ),
        encoding="utf-8",
    )
    base = {"entry": 99.0, "exit_price": 99.5, "exit_time": ""}
    out = merge_reconciliation_into_trade_payload(tmp_path, tid, dict(base))
    assert out["entry"] == 100.25
    assert float(out["exit_price"]) == 102.10
    assert "_recon_status" in out


def test_merge_filled_open_strips_exit(tmp_path: Path) -> None:
    lst = tmp_path / "data" / "runtime" / "fills_reconciliation_last.json"
    lst.parent.mkdir(parents=True, exist_ok=True)
    tid = "c" * 22
    lst.write_text(
        json.dumps(
            {"trades": [{"trade_id": tid, "status": "filled_open", "entry_fill_price": 50.0}]}
        ),
        encoding="utf-8",
    )
    obj = merge_reconciliation_into_trade_payload(
        tmp_path,
        tid,
        {"entry": 48.0, "exit_price": 55.0, "exit_time": "2099"},
    )
    assert obj.get("exit_price") is None
    assert obj.get("exit_time") is None
