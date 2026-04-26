"""Settings / Reports disk table labels (Prompt 13BT-CACHE-NOTE)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot.data_lifecycle import data_dir_line
from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(exist_ok=True)
    return tmp_path


def _client(tmp: Path) -> TestClient:
    (tmp / "data").mkdir(exist_ok=True)
    st = LocalFileStateStore(tmp)
    q = LocalCommandRunner(
        project_root=tmp,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=tmp / "audit_data_labels.jsonl",
    )
    return TestClient(create_app(project_root=tmp, state_store=st, command_queue=q))


def test_data_dir_line_candles_and_backtests() -> None:
    t, h = data_dir_line("data/candles")
    assert "Candle" in t
    assert "candles" in t.lower() or "1m" in t
    t2, h2 = data_dir_line("data/backtests")
    assert "Backtest" in t2
    assert "chart" in t2.lower() or "charts" in h2.lower()


def test_settings_page_shows_disk_labels(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/settings")
    assert r.status_code == 200
    t = r.text
    assert "data/candles" in t
    assert "What it is" in t
    assert "Candle cache" in t
    assert "data/backtests" in t
    assert "gitignored" in t.lower() or "Git" in t


def test_reports_page_shows_candle_and_backtest_rows(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/reports")
    assert r.status_code == 200
    t = r.text
    assert "Data on disk" in t
    assert "data/candles" in t
    assert "data/backtests" in t
    assert "Candle cache" in t or "Candle" in t


def test_settings_render_no_broker_subprocess(empty_project: Path) -> None:
    proj_repr = repr(str(empty_project))
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"proj = Path({proj_repr})\n"
        "from fastapi.testclient import TestClient\n"
        "from bot_ui.app import create_app\n"
        "from bot_ui.services.command_queue import LocalCommandRunner\n"
        "from bot_ui.services.state_store import LocalFileStateStore\n"
        "import sys as _s\n"
        "state = LocalFileStateStore(proj)\n"
        "queue = LocalCommandRunner(project_root=proj, python_executable=_s.executable, "
        "timeout_seconds=5, audit_file=proj / 'a.jsonl')\n"
        "app = create_app(project_root=proj, state_store=state, command_queue=queue)\n"
        "client = TestClient(app)\n"
        "for url in ('/settings', '/reports'):\n"
        "    r = client.get(url)\n"
        "    assert r.status_code == 200, url\n"
        "loaded = sorted(m for m in sys.modules if m == 'bot.broker' or m == 'bot.ibkr_client' "
        "or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "print(json.dumps(loaded))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == []


def test_data_status_cli_json_has_categories() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "data-status", "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    rels = {d.get("path") for d in body.get("dirs", [])}
    assert "data/candles" in rels
    assert "data/backtests" in rels
