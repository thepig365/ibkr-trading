"""Prompt 13H — automated checks for end-to-end paper workflow invariants."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.safety import (
    ALLOWED_COMMANDS,
    validate_args_for,
    is_forbidden,
)
from bot_ui.services.state_store import LocalFileStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

SECRET_PAT = re.compile(
    r"(api[_-]?key|bot[_-]?token|TELEGRAM_BOT_TOKEN|password|secret)\s*=\s*\S+",
    re.IGNORECASE,
)

# Representative paths that must stay ignored (see .gitignore).
GITIGNORE_PROBE_PATHS: tuple[str, ...] = (
    "memory/RESEARCH-REPORT.md",
    "data/research/dummy-probe.json",
    "data/runtime/dummy-probe",
    "data/paper_orders/dummy-probe.jsonl",
    "logs/dummy-probe.log",
    "data/backtests/intraday/dummy.json",
    "data/candles/dummy.csv",
    "data/intraday_smc/dummy.json",
    "data/watchlists/dummy.json",
    "data/debug_charts/dummy.png",
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


def test_engine_status_json_no_broker_no_ibkr() -> None:
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
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data.get("ok") is True
    assert data.get("paper_only") is True
    arts = data.get("artifacts") or {}
    assert "latest_research_json" in arts
    assert "latest_research_instructions" in arts
    assert "latest_backtest_summary" in arts
    assert "paper_forward_test" in data


def test_doctor_script_no_secret_patterns() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "strategy_lab_doctor.sh")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0
    assert not SECRET_PAT.search(out)


def test_api_run_command_accepts_allowlisted_post(project: Path) -> None:
    c = _client(project)
    r = c.post(
        "/api/commands/run",
        data={
            "command": "engine-status",
            "args": "--json",
            "return_to": "/paper",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers.get("location", "").startswith("/paper")


def test_core_ui_commands_allowlisted() -> None:
    """Buttons on Research / Watchlist / Signals / Backtest / Paper must use allowlisted names."""
    required = {
        "research-report",
        "research-status",
        "build-watchlist",
        "scan-intraday-smc-watchlist",
        "backtest-intraday-smc",
        "backtest-report",
        "paper-reconcile",
        "intraday-paper-status",
        "auto-paper-intraday-smc",
        "engine-status",
    }
    assert required.issubset(ALLOWED_COMMANDS.keys())


def test_paper_page_command_forms_validate() -> None:
    for command, args in [
        ("paper-reconcile", ()),
        ("intraday-paper-status", ()),
        ("auto-paper-intraday-smc", ("--source", "dynamic", "--limit", "20")),
        ("refresh-paper-account-state", ()),
    ]:
        ok, msg = validate_args_for(command, args)
        assert ok, f"{command}: {msg}"


def test_journal_loads_without_broker(project: Path) -> None:
    r = _client(project).get("/journal")
    assert r.status_code == 200
    assert "Journal" in r.text or "journal" in r.text.lower()


def test_paper_page_shows_skipped_reason_when_state_present(project: Path) -> None:
    runtime = project / "data" / "runtime"
    runtime.mkdir(parents=True)
    loop = {
        "last_status": "skipped",
        "skipped_reasons": ["trading.intraday_paper.enabled=false"],
        "last_reason": "trading.intraday_paper.enabled=false",
        "cycles": 0,
        "last_cycle_utc": "2026-04-25T00:00:00Z",
        "strict_ready_count": 0,
        "aggressive_ready_count": 0,
        "orders_submitted": 0,
        "last_symbols_scanned": [],
    }
    (runtime / "intraday_auto_paper_loop_state.json").write_text(
        json.dumps(loop), encoding="utf-8"
    )
    c = _client(project)
    r = c.get("/paper")
    assert r.status_code == 200
    assert "trading.intraday_paper.enabled" in r.text or "skipped" in r.text.lower()


def test_runtime_paths_gitignored() -> None:
    for rel in GITIGNORE_PROBE_PATHS:
        p = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert p.returncode == 0, f"expected gitignored: {rel}"


def test_healthz_paper_only(project: Path) -> None:
    c = _client(project)
    h = c.get("/healthz")
    assert h.status_code == 200
    assert h.json().get("paper_only") is True


def test_dashboard_render_does_not_import_broker_or_ibkr(project: Path) -> None:
    import sys as _sys
    from typing import Any

    removed: dict[str, Any] = {}
    for key in list(_sys.modules):
        if key in {"bot.broker", "bot.ibkr_client"} or key.startswith("ib_async"):
            removed[key] = _sys.modules.pop(key, None)
    try:
        c = _client(project)
        r = c.get("/dashboard")
        assert r.status_code == 200
        assert "bot.broker" not in _sys.modules
        assert "bot.ibkr_client" not in _sys.modules
    finally:
        _sys.modules.update({k: v for k, v in removed.items() if v is not None})


def test_no_live_trading_or_order_cli_exposed_in_ui_allowlist() -> None:
    assert "place-order" not in ALLOWED_COMMANDS
    assert is_forbidden("place_order")
    assert is_forbidden("run-auto-paper-intraday-loop")
    # Read-only broker views are allowlisted (explicit button only; no shell).
    assert "portfolio" in ALLOWED_COMMANDS
    assert "open-orders" in ALLOWED_COMMANDS


def test_strategies_route_no_place_order_wording(project: Path) -> None:
    r = _client(project).get("/strategies")
    assert r.status_code == 200
    # Safety copy may say "live trading remains disabled"; still forbid enablement phrasing.
    assert not re.search(
        r"\b(enable\s+live|market\s+order)\b",
        r.text,
        re.IGNORECASE,
    )
