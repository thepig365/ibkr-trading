"""Reports page: journal analytics / cumulative R SVG (no broker)."""

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


def test_reports_shows_cumulative_r_section_en(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200
    assert "Trading journal analytics" in r.text
    assert ("Not enough closed trades yet." in r.text) or ("Cumulative R Curve" in r.text)
    if "Cumulative R Curve" in r.text:
        assert "<svg" in r.text.lower()
    if "USD Equity Curve" in r.text:
        assert "USD equity curve is hidden because reliable realized USD" in r.text
    assert "Equity curve (USD)" not in r.text


def test_reports_journal_analytics_zh(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports?lang=zh")
    assert r.status_code == 200
    assert "交易日记分析" in r.text
    assert "已平仓样本不足" in r.text
    assert "记录到平仓交易后" in r.text
