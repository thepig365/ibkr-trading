"""Prompt 13G — launch scripts, engine-status CLI, UI smoke, canonical paths."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

REQUIRED_PATHS: tuple[str, ...] = (
    "/dashboard",
    "/research",
    "/watchlist",
    "/signals",
    "/backtest",
    "/paper",
    "/strategies",
    "/journal",
    "/logs",
    "/settings",
)

LIVE_PAT = re.compile(
    r"\b(enable[-_]?live|live[-_]?trading|place[-_]?order|market\s*order)\b",
    re.IGNORECASE,
)
SECRET_PAT = re.compile(
    r"(api[_-]?key|bot[_-]?token|TELEGRAM_BOT_TOKEN|password|secret)\s*=\s*\S+",
    re.IGNORECASE,
)

SCRIPT_NAMES: tuple[str, ...] = (
    "start_strategy_lab_ui.sh",
    "stop_strategy_lab_ui.sh",
    "status_strategy_lab_ui.sh",
    "open_strategy_lab_ui.sh",
    "strategy_lab_doctor.sh",
)

# macOS Finder double-click launchers — script-backed must use $ROOT/scripts/* only (no baked paths).
MAC_COMMANDS: tuple[str, ...] = (
    "Strategy Lab.command",
    "Start Strategy Lab.command",
    "Stop Strategy Lab.command",
    "Strategy Lab Doctor.command",
)
# Launchers that only open a browser URL (no scripts/ delegation).
MAC_COMMANDS_URL_ONLY: tuple[str, ...] = (
    "Open Strategy Lab.command",
    "Open Strategy Lab Dashboard.command",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


def _client(project_root: Path) -> TestClient:
    state = LocalFileStateStore(project_root)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=20,
        audit_file=project_root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=project_root, state_store=state, command_queue=queue)
    )


def test_scripts_exist_and_executable() -> None:
    for name in SCRIPT_NAMES:
        p = SCRIPTS / name
        assert p.is_file(), name
        mode = p.stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} should be executable"


def test_mac_command_launchers_exist_executable_delegate_no_secrets() -> None:
    for name in MAC_COMMANDS:
        p = REPO_ROOT / name
        assert p.is_file(), name
        assert p.stat().st_mode & stat.S_IXUSR, f"{name} should be executable"
        text = p.read_text(encoding="utf-8")
        assert 'scripts/' in text, name
        assert "bash " in text and "$ROOT/scripts/" in text, f"{name} should invoke scripts under ROOT"
        assert "uvicorn" not in text.lower()
        assert "python3 -m bot_ui" not in text
        assert "place_order" not in text.lower()
        assert not SECRET_PAT.search(text)
        assert not LIVE_PAT.search(text)
    for name in MAC_COMMANDS_URL_ONLY:
        p = REPO_ROOT / name
        assert p.is_file(), name
        assert p.stat().st_mode & stat.S_IXUSR, f"{name} should be executable"
        text = p.read_text(encoding="utf-8")
        assert 'dirname "$0"' in text
        assert "/dashboard" in text
        assert "Documents/Claude" not in text
        assert "start_strategy_lab_ui" not in text
        assert "uvicorn" not in text.lower()
        assert not SECRET_PAT.search(text)
        assert not LIVE_PAT.search(text)
    start = (REPO_ROOT / "Start Strategy Lab.command").read_text(encoding="utf-8")
    assert "start_strategy_lab_ui.sh" in start
    assert "open_strategy_lab_ui.sh" not in start
    assert 'open "$DASH"' in start or "open \"http://" in start
    stop = (REPO_ROOT / "Stop Strategy Lab.command").read_text(encoding="utf-8")
    assert "stop_strategy_lab_ui.sh" in stop
    openf = (REPO_ROOT / "Open Strategy Lab.command").read_text(encoding="utf-8")
    assert "open_strategy_lab_ui.sh" not in openf
    assert "start_strategy_lab_ui" not in openf
    docf = (REPO_ROOT / "Strategy Lab Doctor.command").read_text(encoding="utf-8")
    assert "strategy_lab_doctor.sh" in docf
    one = (REPO_ROOT / "Strategy Lab.command").read_text(encoding="utf-8")
    assert "start_strategy_lab_ui.sh" in one
    assert "open_strategy_lab_ui.sh" in one
    assert 'dirname "$0"' in one
    assert "healthz" in one
    assert "first-paper-pass" not in one.lower()
    assert "run-auto-paper-intraday-loop" not in one.lower()
    assert "uvicorn" not in one.lower()
    # Must not embed an absolute home path; repo root comes from this script's location.
    assert "/Users/" not in one
    assert "/home/" not in one


def test_start_script_binds_loopback() -> None:
    text = (SCRIPTS / "start_strategy_lab_ui.sh").read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "--host" in text


def test_start_script_does_not_reference_live_trading() -> None:
    text = (SCRIPTS / "start_strategy_lab_ui.sh").read_text(encoding="utf-8")
    assert not LIVE_PAT.search(text), "start script should not suggest live enablement"


def test_doctor_script_exists() -> None:
    assert (SCRIPTS / "strategy_lab_doctor.sh").is_file()


def test_doctor_does_not_echo_secret_patterns() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    p = subprocess.run(
        ["bash", str(SCRIPTS / "strategy_lab_doctor.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    out = p.stdout + p.stderr
    assert not SECRET_PAT.search(out), "doctor must not print secret key=value style lines"
    assert p.returncode == 0


def test_engine_status_cli_no_ibkr() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    p = subprocess.run(
        [sys.executable, "-m", "bot.cli", "engine-status", "--json"],
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
    assert "artifacts" in data
    assert "ui_process" in data
    assert "paper_forward_test" in data


def test_ui_pages_200(project: Path) -> None:
    c = _client(project)
    for path in REQUIRED_PATHS:
        r = c.get(path)
        assert r.status_code == 200, path
        assert "Strategy Lab" in r.text
        if path == "/settings":
            assert "strategy-lab-user-manual" in r.text


def test_healthz_paper_only(project: Path) -> None:
    c = _client(project)
    h = c.get("/healthz")
    assert h.status_code == 200
    assert h.json().get("paper_only") is True


def test_ui_render_no_broker_or_ibkr_import(project: Path) -> None:
    import sys as _sys
    from typing import Any

    removed: dict[str, Any] = {}
    for key in list(_sys.modules):
        if key in {"bot.broker", "bot.ibkr_client"} or key.startswith("ib_async"):
            removed[key] = _sys.modules.pop(key, None)
    try:
        _c = _client(project)
        r = _c.get("/dashboard")
        assert r.status_code == 200
        assert "bot.broker" not in _sys.modules
        assert "bot.ibkr_client" not in _sys.modules
    finally:
        _sys.modules.update({k: v for k, v in removed.items() if v is not None})


def test_runtime_paths_canonical_in_engine_status() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    p = subprocess.run(
        [sys.executable, "-m", "bot.cli", "engine-status", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    d = json.loads(p.stdout)
    rp = d.get("runtime_paths") or {}
    assert "kill_switch" in rp
    assert "mtf_auto_paper" in rp
    assert "intraday_auto_paper" in rp
