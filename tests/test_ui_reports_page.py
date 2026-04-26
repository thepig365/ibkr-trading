"""Prompt 13K — /reports tolerates missing artifacts."""

from __future__ import annotations

import sys
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


def test_reports_page_200_with_no_report_files(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200, r.text
    assert "Strategy Lab" in r.text
    assert "No " in r.text or "—" in r.text or "report" in r.text.lower()


def test_report_commands_allowlisted() -> None:
    assert "paper-daily-report" in ALLOWED_COMMANDS
    assert "paper-weekly-report" in ALLOWED_COMMANDS
    assert "edge-profile-report" in ALLOWED_COMMANDS
    assert "backtest-report" in ALLOWED_COMMANDS
