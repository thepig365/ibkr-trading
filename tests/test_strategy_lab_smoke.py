"""Prompt 13G — Strategy Lab end-to-end smoke (no TWS, no orders).

* UI: TestClient over core pages + /healthz
* Engine: ``python -m bot.cli strategy-lab-engine-status --json``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pages required for 13G “usable” check (200 + common shell chrome).
REQUIRED_GET_PATHS: tuple[str, ...] = (
    "/dashboard",
    "/watchlist",
    "/signals",
    "/research",
    "/backtest",
    "/paper",
    "/journal",
    "/strategies",
    "/settings",
    "/logs",
)

PAGE_SNIPPET = "Strategy Lab"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


def _client(project_root: Path) -> TestClient:
    state = LocalFileStateStore(project_root)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=project_root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=project_root, state_store=state, command_queue=queue)
    )


def test_strategy_lab_ui_pages_and_healthz(project: Path) -> None:
    c = _client(project)
    for path in REQUIRED_GET_PATHS:
        r = c.get(path)
        assert r.status_code == 200, path
        assert PAGE_SNIPPET in r.text, path
    h = c.get("/healthz")
    assert h.status_code == 200
    data = h.json()
    assert data.get("status") == "ok"
    assert data.get("paper_only") is True
    # Root → dashboard (test client can follow redirects; assert redirect on raw GET)
    root = c.get("/", follow_redirects=False)
    assert root.status_code in (302, 307)


def test_cli_strategy_lab_engine_status_json() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    p = subprocess.run(
        [sys.executable, "-m", "bot.cli", "strategy-lab-engine-status", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    data = json.loads(p.stdout)
    assert data.get("ok") is True
    assert data.get("paper_only") is True
    assert data.get("live_trading") is False
    assert "registry_keys" in (data.get("strategies") or {})
    assert "ui" in data and "default_base_url" in data["ui"]


def test_create_app_does_not_import_broker_in_smoke_path(project: Path) -> None:
    import importlib
    import sys as _sys

    removed = {}
    for key in list(_sys.modules):
        if key in {"bot.broker", "bot.ibkr_client"} or key.startswith("ib_async"):
            removed[key] = _sys.modules.pop(key, None)
    try:
        _ = _client(project)
        for bad in ("bot.broker", "bot.ibkr_client"):
            assert bad not in _sys.modules
    finally:
        _sys.modules.update({k: v for k, v in removed.items() if v is not None})
        importlib.invalidate_caches()
