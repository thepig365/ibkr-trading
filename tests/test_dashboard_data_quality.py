"""Dashboard Data Quality card (file-only ledger; no broker)."""

from __future__ import annotations

import json
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


def test_dashboard_data_quality_card_en(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Data Quality" in r.text
    assert "No trade records yet." in r.text
    assert "Closed trades with exit data" not in r.text


def test_dashboard_data_quality_has_field_labels_when_ledger_nonempty(
    tmp_path: Path,
) -> None:
    """When paper_orders rows exist (fixture), field labels render."""

    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    p = pod / "2026-test-intraday-paper-orders.jsonl"
    row = {
        "symbol": "SPY",
        "timestamp": "2026-01-02T15:30:00",
        "submitted": True,
        "strategy_id": "t",
        "signal_category": "x",
        "direction": "long",
        "entry": 100.0,
        "stop": 99.0,
        "exit_time": "2026-01-02T16:00:00",
        "exit_price": 101.0,
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")

    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Closed trades with exit data" in r.text
    assert "Trades missing exit" in r.text


def test_dashboard_data_quality_zh(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "数据质量" in r.text
    assert "暂无交易记录" in r.text
