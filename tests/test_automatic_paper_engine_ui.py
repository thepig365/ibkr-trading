"""UI surface for automatic paper engine (no IBKR on GET)."""

from __future__ import annotations

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


def test_paper_page_lists_engine_section(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/paper")
    assert r.status_code == 200, r.text
    assert "Automatic Paper Trading Engine" in r.text
    assert "run-automatic-paper-engine" in r.text


def test_dashboard_lists_engine_section(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/dashboard")
    assert r.status_code == 200, r.text
    assert "Automatic Paper Trading Engine" in r.text
