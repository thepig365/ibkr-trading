"""/trades + /reports expose allowlisted Complete trade charts buttons (no IBKR on GET)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def cli_project(tmp_path: Path) -> TestClient:
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


def test_trades_page_shows_completion_buttons_zh(cli_project: TestClient) -> None:
    r = cli_project.get("/trades?lang=zh")
    assert r.status_code == 200
    assert "补齐交易复盘图" in r.text
    assert "用本地K线生成交易图" in r.text


def test_reports_page_shows_complete_trade_chart_action(cli_project: TestClient) -> None:
    r = cli_project.get("/reports")
    assert r.status_code == 200
    assert "complete-trade-charts" in r.text


def test_api_trades_allowed_return_prefix() -> None:
    from bot_ui.routes import api as api_mod

    ok = getattr(api_mod, "_ALLOWED_PATH_PREFIXES", frozenset())
    assert "/trades" in ok
