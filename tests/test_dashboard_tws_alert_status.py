"""Dashboard / Paper show TWS Telegram alert summary (file-backed)."""

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


def test_dashboard_renders_tws_alert_card(tmp_path: Path) -> None:
    rtext = _client(tmp_path).get("/dashboard").text
    assert "TWS health" in rtext or "TWS" in rtext


def test_paper_page_renders_tws_card(tmp_path: Path) -> None:
    rtext = _client(tmp_path).get("/paper").text
    assert "TWS health" in rtext or "TWS" in rtext
