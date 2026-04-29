"""Forex page routes mounted."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def forex_client(tmp_path: Path) -> TestClient:
    (tmp_path / "data").mkdir()
    state = LocalFileStateStore(tmp_path)
    queue = LocalCommandRunner(
        project_root=tmp_path,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=tmp_path / "ui_audit.jsonl",
    )
    app = create_app(project_root=tmp_path, state_store=state, command_queue=queue)
    return TestClient(app)


def test_forex_page_route_returns_200(forex_client: TestClient) -> None:
    r = forex_client.get("/forex")
    assert r.status_code == 200
    assert "Forex" in r.text
