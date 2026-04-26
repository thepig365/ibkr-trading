"""UI shows latest paper report paths when present (Prompt 13M)."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


REPO = Path(__file__).resolve().parent.parent


def _client(root: Path) -> TestClient:
    st = LocalFileStateStore(root)
    q = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(create_app(project_root=root, state_store=st, command_queue=q))


def test_dashboard_shows_paper_report_paths(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    rep = tmp_path / "data" / "reports" / "paper"
    rep.mkdir(parents=True)
    (rep / "2026-01-01-paper-daily-report.md").write_text("# x", encoding="utf-8")
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    assert "2026-01-01-paper-daily-report.md" in r.text
    assert "data/reports/paper" in r.text


def test_logs_page_mentions_paper_reports(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    rep = tmp_path / "data" / "reports" / "paper"
    rep.mkdir(parents=True)
    (rep / "z-paper-daily-report.md").write_text("# z", encoding="utf-8")
    r = _client(tmp_path).get("/logs")
    assert r.status_code == 200
    assert "paper-daily-report.md" in r.text
