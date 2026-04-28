"""/journal points to trader-facing Trade Records; sidebar includes Trades."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

from tests.test_journal_page import _write_paper_order


@pytest.fixture
def cli_proj(tmp_path: Path) -> tuple[TestClient, Path]:
    (tmp_path / "data").mkdir()
    state = LocalFileStateStore(tmp_path)
    queue = LocalCommandRunner(
        project_root=tmp_path,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=tmp_path / "ui_audit.jsonl",
    )
    return TestClient(create_app(project_root=tmp_path, state_store=state, command_queue=queue)), tmp_path


def test_journal_page_links_view_trade_zh(cli_proj: tuple[TestClient, Path]) -> None:
    cli, proj = cli_proj
    _write_paper_order(proj)
    r = cli.get("/journal?lang=zh")
    assert r.status_code == 200
    assert "/trades/" in r.text
    assert "查看本笔交易" in r.text


def test_journal_banner_mentions_trade_records_zh(cli_proj: tuple[TestClient, Path]) -> None:
    cli, _proj = cli_proj
    r = cli.get("/journal?lang=zh")
    assert r.status_code == 200
    assert "交易记录" in r.text


def test_base_sidebar_includes_trades_nav(cli_proj: tuple[TestClient, Path]) -> None:
    cli, _proj = cli_proj
    r = cli.get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "交易记录" in r.text
    html = r.text
    idx = html.find("/trades")
    assert idx != -1, "Expected /trades link in sidebar"
