"""Dashboard trader cockpit broker snapshot UX."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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


def test_dashboard_connect_button_and_zh_labels(tmp_path: Path) -> None:
    """Prominent trader cockpit strings + bilingual labels."""
    c = _client(tmp_path)
    r = c.get("/dashboard")
    assert r.status_code == 200
    assert "broker-snapshot-refresh" in r.text
    assert "Connect / Refresh TWS" in r.text or "broker-snapshot" in r.text

    z = c.get("/dashboard?lang=zh")
    assert z.status_code == 200
    assert "连接" in z.text and "刷新" in z.text


def test_dashboard_no_snapshot_banner(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/dashboard").text
    assert "Not checked yet" in body


def test_dashboard_with_cached_snapshot_cards(tmp_path: Path) -> None:
    rt = tmp_path / "data" / "runtime"
    rt.mkdir(parents=True)
    snap_path = rt / "broker_snapshot_last.json"
    snap_path.write_text(
        json.dumps(
            {
                "checked_at_utc": "2026-04-27T12:00:00Z",
                "status": "ok",
                "positions_count": 2,
                "open_orders_count": 1,
                "executions_count": 10,
                "positions": [],
                "open_orders": [],
                "recent_executions": [],
                "account_mode": "paper",
                "ibkr_connected": True,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    txt = _client(tmp_path).get("/dashboard").text
    assert "2026-04-27T12:00:00Z" in txt
    assert "paper" in txt


@pytest.mark.parametrize(
    "pattern",
    [
        "Submitted records exist",
        "local engine records",
    ],
)
def test_dashboard_explain_when_submitted_but_no_broker_positions(
    tmp_path: Path, pattern: str
) -> None:
    pod = tmp_path / "data" / "paper_orders"
    pod.mkdir(parents=True)
    row = {
        "symbol": "QQQ",
        "timestamp": "2026-04-01T15:30:00",
        "submitted": True,
        "direction": "long",
        "strategy_id": "x",
        "signal_category": "x",
        "entry": 100.0,
        "stop": 99.0,
        "skipped_reasons": [],
    }
    (pod / "2026-04-01-intraday-paper-orders.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    rt = tmp_path / "data" / "runtime"
    rt.mkdir(parents=True)
    (rt / "broker_snapshot_last.json").write_text(
        json.dumps(
            {
                "checked_at_utc": "2026-04-02T01:02:03Z",
                "status": "ok",
                "positions_count": 0,
                "positions": [],
                "open_orders_count": 0,
                "open_orders": [],
                "executions_count": 0,
                "recent_executions": [],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    txt = _client(tmp_path).get("/dashboard").text
    assert pattern in txt
