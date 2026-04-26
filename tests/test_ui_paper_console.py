"""Prompt 13I — paper control panel: budgets, first-paper button, no auto-loop."""

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


def test_paper_page_shows_budget_and_safety_gates(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/paper")
    assert r.status_code == 200
    t = r.text
    for chunk in (
        "daily cap",
        "per-trade cap",
        "first-paper-pass",
        "PAPER",
    ):
        assert chunk.lower() in t.lower() or chunk in t, chunk


def test_first_paper_pass_button_exists_only_as_explicit_form(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/paper")
    assert r.status_code == 200
    assert "first-paper-pass" in r.text
    assert "run-auto-paper-intraday-loop" not in r.text

