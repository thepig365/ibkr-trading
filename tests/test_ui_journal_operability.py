"""Prompt 13J — journal shows bracket integrity, sizing, TIF (read-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.log_reader import mask_secrets
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_journal_renders_paper_row_with_bracket_and_sizing(tmp_path: Path) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    logf = pod / "2026-04-24-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-24T14:00:00Z",
        "symbol": "NVDA",
        "strategy_id": "ict_smc_intraday_v1",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "direction": "long",
        "submitted": False,
        "submitted_to_broker": False,
        "bracket_integrity": "incomplete",
        "bracket_protected": True,
        "higher_timeframe_context_ok": True,
        "five_min_setup_found": True,
        "one_min_trigger_found": False,
        "edge_audit": {"edge_score": 55.0, "recommended_mode": "strict"},
        "parent_entry_order_id": 1001,
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "original_entry": 100.01,
        "min_tick": "0.01",
        "min_tick_source": "contract",
        "tif": "DAY",
        "estimated_notional": 500.0,
        "sizing_audit": {
            "per_trade_notional_cap_applied": True,
            "daily_cap_reached": False,
        },
    }
    logf.write_text(json.dumps(row) + "\n", encoding="utf-8")

    r = _client(tmp_path).get("/journal")
    assert r.status_code == 200
    t = r.text
    assert "NVDA" in t
    assert "incomplete" in t
    assert "minTick" in t or "0.01" in t
    assert "Est. notional" in t or "500" in t
    assert "55" in t or "strict" in t
    assert "1001" in t or "Prot" in t

    r2 = _client(tmp_path).get("/journal?filter=incomplete&symbol=NVDA")
    assert r2.status_code == 200
    assert "NVDA" in r2.text


def test_log_mask_strips_telegram_token_like_strings() -> None:
    raw = (
        "bot=123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    masked = mask_secrets(raw)
    assert "***REDACTED-TG-TOKEN***" in masked
    assert "abcdefghijklmnopqrstuvwxyzAB" not in masked
