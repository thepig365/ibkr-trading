"""UI routes: strategy control center and selectors (13STRATEGY-UI)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

def _client(tmp: Path) -> TestClient:
    (tmp / "data").mkdir(exist_ok=True)
    st = LocalFileStateStore(tmp)
    q = LocalCommandRunner(
        project_root=tmp,
        python_executable=sys.executable,
        timeout_seconds=20,
        audit_file=tmp / "a.jsonl",
    )
    return TestClient(create_app(project_root=tmp, state_store=st, command_queue=q))


def test_strategies_200_ict_shown(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/strategies")
    assert r.status_code == 200
    t = r.text
    assert "ICT/SMC Intraday" in t or "ict_smc_intraday_v1" in t


def test_chanlun_stub_not_paper_enabled(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/strategies")
    t = r.text
    assert "Chanlun" in t
    assert "not for paper" in t.lower() or "disabled" in t.lower() or "future" in t.lower()


def test_paper_shows_paper_strategy_line(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/paper")
    assert r.status_code == 200
    t = r.text
    assert "paper strategy" in t.lower() or "Paper trading strategy" in t
    assert "1-minute" in t or "1 minute" in t.lower() or "1m" in t.lower()


def test_signals_includes_ict_smc_in_content(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/signals")
    assert r.status_code == 200
    assert "ict_smc_intraday_v1" in r.text
