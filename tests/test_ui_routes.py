"""Smoke + behaviour tests for the FastAPI routes."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import (
    CommandRequest,
    CommandResult,
    LocalCommandRunner,
)
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Empty project layout the LocalFileStateStore will silently handle."""
    (tmp_path / "data").mkdir()
    return tmp_path


@pytest.fixture
def fake_cli_project(tmp_path: Path) -> Path:
    """Project with a stub bot.cli that just echoes argv as JSON."""
    (tmp_path / "bot").mkdir()
    (tmp_path / "bot" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "bot" / "cli.py").write_text(
        "import sys, json\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    return tmp_path


def _client(project_root: Path) -> TestClient:
    state = LocalFileStateStore(project_root)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=project_root / "ui_audit.jsonl",
    )
    app = create_app(project_root=project_root, state_store=state, command_queue=queue)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET pages: every page renders 200 even with an empty project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/dashboard", "/watchlist", "/signals", "/paper", "/research", "/logs", "/settings"],
)
def test_pages_render_200_with_empty_project(project: Path, path: str) -> None:
    client = _client(project)
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert "Strategy Lab" in r.text


def test_root_redirects_to_dashboard(project: Path) -> None:
    r = _client(project).get("/", follow_redirects=False)
    assert r.status_code in {301, 302, 307}
    assert r.headers["location"].endswith("/dashboard")


def test_healthz_reports_paper_only(project: Path) -> None:
    r = _client(project).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["paper_only"] is True
    assert body["host"] == "127.0.0.1"
    assert body["port"] == 8765


def test_unknown_path_returns_404_template(project: Path) -> None:
    r = _client(project).get("/does-not-exist")
    assert r.status_code == 404
    assert "Not Found" in r.text


# ---------------------------------------------------------------------------
# POST /api/commands/run: allowlist enforcement at the route level
# ---------------------------------------------------------------------------


def test_post_run_command_allowlisted_executes(fake_cli_project: Path) -> None:
    client = _client(fake_cli_project)
    r = client.post(
        "/api/commands/run",
        data={"command": "paper-reconcile", "args": "--limit 1", "return_to": "/paper"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/paper"
    # The runner ring buffer must show one OK row.
    runner = client.app.state.command_queue
    last = runner.list_recent(1)[0]
    assert last.accepted is True
    assert last.exit_code == 0
    payload = json.loads(last.stdout.strip())
    assert payload["argv"] == ["paper-reconcile", "--limit", "1"]


def test_post_run_command_rejects_unallowlisted(fake_cli_project: Path) -> None:
    client = _client(fake_cli_project)
    r = client.post(
        "/api/commands/run",
        data={"command": "auto-paper-mtf", "return_to": "/dashboard"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    runner = client.app.state.command_queue
    last = runner.list_recent(1)[0]
    assert last.accepted is False
    assert last.exit_code is None  # never executed


def test_post_run_command_redirects_only_to_known_pages(fake_cli_project: Path) -> None:
    client = _client(fake_cli_project)
    r = client.post(
        "/api/commands/run",
        data={"command": "paper-reconcile", "return_to": "https://evil.example.com/x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard"  # falls back to safe default


def test_post_run_command_rejects_shell_metachars_in_args(fake_cli_project: Path) -> None:
    client = _client(fake_cli_project)
    r = client.post(
        "/api/commands/run",
        data={"command": "paper-reconcile", "args": "--foo $(rm -rf /)", "return_to": "/paper"},
    )
    assert r.status_code in {200, 303}
    runner = client.app.state.command_queue
    last = runner.list_recent(1)[0]
    assert last.accepted is False


# ---------------------------------------------------------------------------
# POST /paper/runtime/* — toggles on-disk runtime files
# ---------------------------------------------------------------------------


def test_kill_switch_toggle_creates_and_removes_file(project: Path) -> None:
    """UI must write to the canonical worker path ``data/KILL_SWITCH``,
    NOT ``data/runtime/KILL_SWITCH`` — otherwise the auto-paper loop and
    Telegram /kill /resume would disagree with the UI on safety state.
    """
    client = _client(project)
    target = project / "data" / "KILL_SWITCH"
    legacy = project / "data" / "runtime" / "KILL_SWITCH"
    assert not target.exists()
    r = client.post("/paper/runtime/kill-switch", data={"enable": "on"}, follow_redirects=False)
    assert r.status_code == 303
    assert target.exists(), "UI must create the canonical data/KILL_SWITCH file"
    assert not legacy.exists(), "UI must NOT write to data/runtime/KILL_SWITCH (legacy path)"
    r = client.post("/paper/runtime/kill-switch", data={"enable": "off"}, follow_redirects=False)
    assert r.status_code == 303
    assert not target.exists()


def test_mtf_auto_toggle_writes_expected_value(project: Path) -> None:
    client = _client(project)
    target = project / "data" / "runtime" / "mtf_auto_paper_enabled"
    client.post("/paper/runtime/mtf-auto", data={"state": "on"}, follow_redirects=False)
    assert target.read_text(encoding="utf-8").strip() == "1"
    client.post("/paper/runtime/mtf-auto", data={"state": "off"}, follow_redirects=False)
    assert target.read_text(encoding="utf-8").strip() == "0"


# ---------------------------------------------------------------------------
# Logs page — secret masking + path-confinement
# ---------------------------------------------------------------------------


def test_logs_page_masks_telegram_token(project: Path) -> None:
    log = project / "logs" / "auto_paper.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    secret = "123456789:AAGq" + "a" * 32
    log.write_text(f"using TELEGRAM_BOT_TOKEN={secret}\n", encoding="utf-8")
    client = _client(project)
    r = client.get(f"/logs?file=logs/auto_paper.log")
    assert r.status_code == 200
    assert secret not in r.text
    assert "REDACTED" in r.text


def test_logs_page_refuses_to_read_outside_project(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    other = tmp_path_factory.mktemp("outside")
    leak = other / "secret.log"
    leak.write_text("nope\n", encoding="utf-8")
    client = _client(project)
    r = client.get(f"/logs?file={leak}")
    assert r.status_code == 200
    assert "Refusing to read" in r.text or "outside" in r.text


# ---------------------------------------------------------------------------
# Dashboard with real (synthetic) state shows expected content
# ---------------------------------------------------------------------------


def test_dashboard_renders_account_and_signals(project: Path) -> None:
    snap = project / "data" / "account_snapshots.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(
        json.dumps(
            {
                "ts_utc": "2026-04-24T15:00:00Z",
                "account_id": "DUTEST",
                "net_liquidation": 1234.56,
                "currency": "AUD",
            }
        ),
        encoding="utf-8",
    )
    sig = project / "data" / "mtf_smc" / "2026-04-24-watchlist-mtf-smc-summary.json"
    sig.parent.mkdir(parents=True, exist_ok=True)
    sig.write_text(
        json.dumps(
            {
                "date": "2026-04-24",
                "source": "dynamic",
                "symbols_scanned": 1,
                "counts": {"FULL_ALIGNMENT": 1},
                "top_by_alignment_score": [
                    {"symbol": "NVDA", "mtf_alignment_score": 80,
                     "alignment_category": "FULL_ALIGNMENT",
                     "eligible_for_future_paper_trade": True}
                ],
                "eligible_for_future_paper_trade": ["NVDA"],
            }
        ),
        encoding="utf-8",
    )
    r = _client(project).get("/dashboard")
    assert r.status_code == 200
    assert "DUTEST" in r.text
    assert "1,234.56" in r.text
    assert "NVDA" in r.text
    assert "FULL_ALIGNMENT" in r.text
    assert "PAPER ONLY" in r.text
