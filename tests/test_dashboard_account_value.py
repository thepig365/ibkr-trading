"""Account value cards from cached broker snapshot (GET is file-only)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
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


def test_dashboard_account_value_heading_and_truth_section(tmp_path: Path) -> None:
    txt = _client(tmp_path).get("/dashboard").text
    assert "Account Value" in txt or "broker truth" in txt.lower()
    assert "Broker Truth" in txt
    assert "Local Engine Records" in txt


def test_dashboard_not_checked_placeholder(tmp_path: Path) -> None:
    txt = _client(tmp_path).get("/dashboard").text
    assert "Not checked yet" in txt


def test_dashboard_net_liquidation_from_snapshot(tmp_path: Path) -> None:
    rt = tmp_path / "data" / "runtime"
    rt.mkdir(parents=True)
    metrics = {"net_liquidation": 10000.51, "available_funds": 5000.0}
    path = rt / "broker_snapshot_last.json"
    path.write_text(
        json.dumps(
            {
                "checked_at_utc": "2026-04-27T12:00:00Z",
                "status": "ok",
                "positions_count": 0,
                "positions": [],
                "open_orders_count": 0,
                "open_orders": [],
                "executions_count": 0,
                "recent_executions": [],
                "ibkr_connected": True,
                "meta": {"account_metrics": metrics},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    txt = _client(tmp_path).get("/dashboard").text
    assert "10,000.51" in txt or "$10,000.51" in txt


def test_dashboard_zh_truth_copy(tmp_path: Path) -> None:
    txt = _client(tmp_path).get("/dashboard?lang=zh").text
    assert "券商真实状态" in txt
    assert "本地引擎记录" in txt
