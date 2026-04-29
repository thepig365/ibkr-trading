"""Dashboard trade context exposes fills reconciliation file read (no broker import)."""

from __future__ import annotations

import json
from pathlib import Path

from bot.trade_reports import build_dashboard_trade_context


def test_dashboard_fills_reconciliation_none_without_file(tmp_path: Path) -> None:
    ctx = build_dashboard_trade_context(tmp_path)
    assert ctx.get("fills_reconciliation") in (None, {})


def test_dashboard_fills_reconciliation_reads_runtime_json(tmp_path: Path) -> None:
    p = tmp_path / "data" / "runtime" / "fills_reconciliation_last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"reconciled_at_utc": "2026-04-01T12:00:00Z", "fills_count": 2}),
        encoding="utf-8",
    )
    ctx = build_dashboard_trade_context(tmp_path)
    fr = ctx.get("fills_reconciliation")
    assert isinstance(fr, dict)
    assert fr.get("fills_count") == 2
