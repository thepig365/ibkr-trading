"""/trades UI — read-only, no broker."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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


def test_trades_page_returns_200(cli_project):
    cli, _root = cli_project
    r = cli.get("/trades")
    assert r.status_code == 200


def test_trades_lang_zh_title(cli_project):
    cli, _root = cli_project
    r = cli.get("/trades?lang=zh")
    assert r.status_code == 200
    assert "交易记录" in r.text


def test_trades_route_module_does_not_import_broker_or_ibkr() -> None:
    to_clear = [
        k
        for k in list(sys.modules)
        if k in {"bot.broker", "bot.ibkr_client"}
        or k.startswith("ib_async")
        or k.startswith("ib_insync")
    ]
    removed: dict[str, Any] = {k: sys.modules.pop(k) for k in to_clear if k in sys.modules}
    try:
        importlib.import_module("bot_ui.routes.trades")
        leaked = [
            m
            for m in sys.modules
            if m in {"bot.broker", "bot.ibkr_client"}
            or m.startswith("ib_async")
            or m.startswith("ib_insync")
        ]
        assert leaked == [], f"/trades route leaked broker imports: {leaked}"
    finally:
        sys.modules.update(removed)


def test_trades_table_with_row(cli_project):
    cli, proj = cli_project
    pod = proj / "data" / "paper_orders"
    pod.mkdir(parents=True)
    lp = pod / "t-intraday-paper-orders.jsonl"
    row = {
        "timestamp": "2026-04-26T14:00:00Z",
        "symbol": "TTT",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": "long",
        "submitted": True,
        "skipped_reasons": [],
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "planned_rr": 2.0,
        "bracket_integrity": "complete",
    }
    lp.write_text(json.dumps(row) + "\n", encoding="utf-8")
    r = cli.get("/trades")
    assert r.status_code == 200
    assert "TTT" in r.text
    assert "查看本笔交易" in r.text or "View Trade" in r.text
