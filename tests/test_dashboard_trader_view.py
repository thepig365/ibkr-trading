"""Trader-facing dashboard primary area (no IBKR on GET)."""

from __future__ import annotations

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


def test_dashboard_trader_command_center_en(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "Trader command center" in r.text
    assert "Quick links" in r.text
    assert "Latest trades" in r.text


def test_dashboard_trader_zh_labels(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "交易员控制台" in r.text
    assert "快捷入口" in r.text


def test_dashboard_diagnostics_collapsed_not_primary(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "<details" in r.text
    tc = r.text
    assert tc.index("Trader command center") < tc.index("Operational snapshot")


def test_dashboard_get_does_not_need_ibkr(tmp_path: Path) -> None:
    """GET /dashboard must not require TWS; empty project has no broker hooks in template."""

    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
