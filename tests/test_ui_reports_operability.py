"""/reports shows disk summary, email status, report paths (read-only, no IBKR)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.safety import ALLOWED_COMMANDS
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


def test_reports_page_shows_data_disk_and_email_block(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200, r.text
    t = r.text
    assert "Data on disk" in t or "bytes" in t
    assert "Report email" in t or "resend" in t.lower() or "email" in t.lower()
    assert "ileonzh@gmail.com" in t or "data/" in t


def test_reports_page_shows_latest_trade_reviews_when_journal_rows_exist(
    tmp_path: Path,
) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    sent = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "ict_smc_intraday_v1",
        "symbol": "SENT",
        "direction": "long",
        "signal_category": "DAY_TRADE_READY_STRICT",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 10.0,
        "stop": 9.0,
        "target": 11.0,
        "order_ids": [1, 2, 3],
        "paper_only": True,
        "live_trading_allowed": False,
        "bracket_integrity": "complete",
    }
    skipped = {
        **sent,
        "symbol": "SKIP",
        "submitted": False,
        "skipped_reasons": ["kill switch"],
    }
    inc = {
        **sent,
        "symbol": "INC",
        "bracket_integrity": "incomplete",
    }
    logf = pod / "2026-01-20-intraday-paper-orders.jsonl"
    logf.write_text(
        "\n".join(
            json.dumps(x) for x in (sent, skipped, inc)
        )
        + "\n",
        encoding="utf-8",
    )
    tid = LocalFileStateStore(tmp_path).get_journal_view(limit=20).paper_orders[
        0
    ].trade_id
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200, r.text
    assert "Latest trade reviews" in r.text
    assert tid in r.text


def test_data_status_and_cleanup_dry_run_allowlisted() -> None:
    assert "data-status" in ALLOWED_COMMANDS
    assert "data-cleanup" in ALLOWED_COMMANDS
