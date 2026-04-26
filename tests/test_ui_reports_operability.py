"""/reports shows disk summary, email status, report paths (read-only, no IBKR)."""

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


def test_reports_page_shows_data_disk_and_email_block(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200, r.text
    t = r.text
    assert "Data on disk" in t or "bytes" in t
    assert "Report email" in t or "resend" in t.lower() or "email" in t.lower()
    assert "ileonzh@gmail.com" in t or "data/" in t


def test_data_status_and_cleanup_dry_run_allowlisted() -> None:
    assert "data-status" in ALLOWED_COMMANDS
    assert "data-cleanup" in ALLOWED_COMMANDS
