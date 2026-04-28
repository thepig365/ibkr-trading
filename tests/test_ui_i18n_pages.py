"""Bilingual page labels (priority routes)."""

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


def test_dashboard_default_english(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/dashboard")
    assert r.status_code == 200
    assert "Dashboard" in r.text or "Report center" in r.text
    assert "交易员控制台" not in r.text


def test_dashboard_zh_shows_chinese(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/dashboard?lang=zh")
    assert r.status_code == 200
    assert "交易员控制台" in r.text
    assert 'lang="zh"' in r.text


def test_paper_zh_engine_heading(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/paper?lang=zh")
    assert r.status_code == 200
    assert "自动纸面交易引擎" in r.text


def test_reports_zh(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/reports?lang=zh")
    assert r.status_code == 200
    assert ("今日报告摘要" in r.text or "报告" in r.text) or "累计 R 曲线" in r.text


def test_backtest_zh_buttons(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/backtest?lang=zh")
    assert r.status_code == 200
    assert "拉取缺失数据并运行回测" in r.text
    assert "检查数据覆盖" in r.text


def test_settings_zh_sections(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/settings?lang=zh")
    assert r.status_code == 200
    assert "后台自动运行器" in r.text
    assert "Telegram 命令监听器" in r.text
