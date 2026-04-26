"""Prompt 13C — dashboard command center copy and structure (no auto-run)."""

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


def test_dashboard_shows_command_center_sections(tmp_path: Path) -> None:
    t = _client(tmp_path).get("/dashboard")
    assert t.status_code == 200
    text = t.text
    for chunk in (
        "Engine health",
        "TWS / IBKR",
        "Paper safety",
        "Trading budget",
        "Intraday signals",
        "PAPER ONLY",
        "allowlisted",
        "engine-status",
        "ibkr-session-status",
        "open-orders",
    ):
        assert chunk in text, chunk


def test_dashboard_does_not_expose_intraday_auto_loop_command(tmp_path: Path) -> None:
    t = _client(tmp_path).get("/dashboard")
    assert t.status_code == 200
    assert "run-auto-paper-intraday-loop" not in t.text
