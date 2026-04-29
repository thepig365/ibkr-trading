"""TradeLedgerRecord overlays from fills reconciliation JSON."""

from __future__ import annotations

import json
from pathlib import Path

from bot.trade_ledger import raw_dict_to_trade_record
from bot.fills_reconciliation import apply_reconciliation_to_records


def test_apply_reconciliation_sets_exit_when_closed(tmp_path: Path) -> None:
    abs_p = str(tmp_path / "day.jsonl")

    row = {
        "timestamp": "2026-04-01T14:00:00Z",
        "symbol": "SPY",
        "strategy_id": "s",
        "direction": "long",
        "signal_category": "x",
        "submitted": True,
        "submitted_to_broker": True,
        "quantity": 10.0,
        "planned_rr": 1.0,
        "skipped_reasons": [],
    }
    rec = raw_dict_to_trade_record(abs_p, 0, row)
    tid = rec.trade_id

    lst = tmp_path / "data" / "runtime" / "fills_reconciliation_last.json"
    lst.parent.mkdir(parents=True, exist_ok=True)
    lst.write_text(
            json.dumps(
                {
                    "trades": [
                        {
                            "trade_id": tid,
                            "status": "closed",
                            "entry_fill_price": 10.0,
                            "entry_fill_time": "2026-04-01T14:01:01+00:00",
                            "exit_fill_price": 11.25,
                            "exit_fill_time": "2026-04-01T15:05:05+00:00",
                            "realized_r": 2.5,
                            "realized_pnl_usd": 125.5,
                            "close_reason": "target_hit",
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    apply_reconciliation_to_records([rec], tmp_path)

    assert rec.exit_price == 11.25
    assert rec.realized_r == 2.5
    assert rec.status_slug == "closed"
