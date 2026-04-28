"""Trade detail /trades/{id} — single trade only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.journal_trade_id import compute_stable_trade_row_id
from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def cli_project(tmp_path: Path) -> tuple[TestClient, Path]:
    (tmp_path / "data").mkdir()
    state = LocalFileStateStore(tmp_path)
    queue = LocalCommandRunner(
        project_root=tmp_path,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=tmp_path / "ui_audit.jsonl",
    )
    app = create_app(project_root=tmp_path, state_store=state, command_queue=queue)
    return TestClient(app), tmp_path


def _tid(proj: Path) -> str:
    pod = proj / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "d-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-26T14:00:00Z",
        "symbol": "ONLY",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 10.0,
        "stop": 9.0,
        "target": 12.0,
        "planned_rr": 2.0,
        "bracket_integrity": "complete",
    }
    raw = json.dumps(row)
    lp.write_text(raw + "\n", encoding="utf-8")
    return compute_stable_trade_row_id(str(lp.resolve()), 0, json.loads(raw)).lower()


def test_trade_detail_shows_only_symbol(cli_project):
    cli, proj = cli_project
    tid = _tid(proj)
    r = cli.get(f"/trades/{tid}")
    assert r.status_code == 200
    body = r.text
    assert "ONLY" in body
    assert "ONLY only" in body or "strong>ONLY" in body or ">ONLY<" in body
    assert body.count("ONLY") >= 1


def test_trade_detail_shows_exit_when_both_fields(cli_project):
    cli, proj = cli_project
    pod = proj / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "cx-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-26T14:00:00Z",
        "symbol": "EX",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 100.0,
        "stop": 99.0,
        "target": 103.0,
        "planned_rr": 1.5,
        "exit_time": "2026-04-26T15:30:00Z",
        "exit_price": 102.25,
        "close_reason": "target_hit",
        "bracket_integrity": "complete",
    }
    raw = json.dumps(row)
    lp.write_text(raw + "\n")
    tid = compute_stable_trade_row_id(str(lp.resolve()), 0, json.loads(raw)).lower()
    r = cli.get(f"/trades/{tid}")
    assert r.status_code == 200
    assert "102.25" in r.text
    assert "Exit not recorded yet" not in r.text


def test_trade_detail_exit_not_recorded_without_exit_fields(cli_project):
    cli, proj = cli_project
    tid = _tid(proj)
    r = cli.get(f"/trades/{tid}")
    assert r.status_code == 200
    assert "Exit not recorded yet" in r.text or "尚未记录平仓" in r.text
