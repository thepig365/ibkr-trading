"""UI copy is human-oriented; key safety lines present."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=30,
        audit_file=root / "data" / "ux_test.jsonl",
    )
    return TestClient(create_app(project_root=root, state_store=state, command_queue=queue))


def test_dashboard_contains_human_headings(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/dashboard")
    assert r.status_code == 200
    t = r.text
    assert "Today’s safety" in t or "safety" in t.lower()
    assert "approved" in t.lower() or "Pre-market" in t


def test_signals_ict_trigger_disclaimer(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/signals?strategy=ict_smc_intraday_v1")
    assert r.status_code == 200
    assert "1-minute" in r.text.lower() or "ICT" in r.text


def test_research_premarket_section(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/research")
    assert r.status_code == 200
    assert "Pre-Market" in r.text


def test_paper_shows_can_test_block(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/paper")
    assert r.status_code == 200
    t = r.text
    assert "Can I run a paper test now?" in t
    assert "PAPER ONLY" in t


def test_journal_page_plain_language_subtitle(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/journal")
    assert r.status_code == 200
    t = r.text
    # Subtitle is always present; per-row table headers only when there are log rows.
    assert "no broker connection" in t.lower()


def test_reports_mentions_premarket_card(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200
    assert "Pre-Market" in r.text
