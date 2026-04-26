"""Backtest page cache + Git copy (Prompt 13BT-CACHE-NOTE)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
        audit_file=tmp / "audit_cache_note.jsonl",
    )
    return TestClient(create_app(project_root=tmp, state_store=st, command_queue=q))


def test_backtest_200_with_cache_explanation(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/backtest")
    assert r.status_code == 200
    t = r.text
    assert "cached locally" in t
    assert "not uploaded to GitHub" in t
    # Chinese line from spec
    assert "本机" in t or "缓存在" in t
    assert "data/candles" in t
    assert "gitignored" in t.lower() or "ignored by Git" in t


def test_backtest_render_no_broker_subprocess(empty_project: Path) -> None:
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
        "r = client.get('/backtest')\n"
        "assert r.status_code == 200\n"
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
