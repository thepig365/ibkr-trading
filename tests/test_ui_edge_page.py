"""Tests for the /edge Strategy Lab page (Prompt 13L-alt)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "data" / "edge_profiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "memory").mkdir(exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        src = REPO_ROOT / "config" / name
        if src.exists():
            shutil.copy(src, tmp_path / "config" / name)
    sample = {
        "date": "2026-01-20",
        "profiles": [
            {
                "symbol": "AAPL",
                "strategy_id": "ict_smc_intraday_v1",
                "edge_score": 50.0,
                "confidence_level": "moderate",
                "recommended_mode": "strict_only",
                "max_risk_multiplier": 0.5,
                "total_signals": 10,
                "filled_trades": 8,
                "fill_rate": 0.8,
                "total_r": 1.0,
                "max_drawdown_r": -2.0,
                "best_direction": "long",
                "best_hours": ["10"],
            }
        ],
    }
    p = tmp_path / "data" / "edge_profiles" / "2026-01-20-edge-profiles.json"
    p.write_text(json.dumps(sample, indent=2), encoding="utf-8")

    st = LocalFileStateStore(tmp_path)
    q = LocalCommandRunner(
        project_root=tmp_path,
        python_executable=sys.executable,
        timeout_seconds=5,
        audit_file=tmp_path / "a.jsonl",
    )
    return TestClient(create_app(project_root=tmp_path, state_store=st, command_queue=q))


def test_edge_page_returns_200(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/edge")
    assert r.status_code == 200
    assert "AAPL" in r.text or "edge" in r.text.lower()


def test_edge_render_does_not_import_broker() -> None:
    code = (
        "import json, importlib, sys\n"
        "import bot_ui.app  # noqa: F401\n"
        "importlib.import_module('bot_ui.routes.edge')\n"
        "loaded = sorted("
        "m for m in sys.modules if m in ('bot.broker', 'bot.ibkr_client') "
        "or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    p = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout.strip()) == [], p.stdout
