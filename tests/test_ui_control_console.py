"""Prompt 13O — control console: routes, allowlist, no intraday auto-loop in UI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import (
    CommandRequest,
    LocalCommandRunner,
    validate_request,
)
from bot_ui.services.safety import ALLOWED_COMMANDS, is_forbidden
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent

MAIN_PATHS: tuple[str, ...] = (
    "/dashboard",
    "/research",
    "/watchlist",
    f"/signals?strategy=ict_smc_intraday_v1",
    "/backtest",
    "/edge",
    "/paper",
    "/journal",
    "/reports",
    "/logs",
    "/strategies",
    "/settings",
)


def _client(root: Path) -> TestClient:
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=15,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


@pytest.mark.parametrize("path", MAIN_PATHS)
def test_main_pages_200_on_empty_project(tmp_path: Path, path: str) -> None:
    (tmp_path / "data").mkdir()
    r = _client(tmp_path).get(path)
    assert r.status_code == 200, (path, r.text[:500])
    assert "Strategy Lab" in r.text


def test_real_repo_data_directory_does_not_break_pages() -> None:
    """With repo layout + any real data/ artifacts, every page should stay 200."""
    if not (REPO / "data").is_dir():
        pytest.skip("no data/ in checkout")
    r = _client(REPO)
    for path in MAIN_PATHS:
        res = r.get(path)
        assert res.status_code == 200, f"{path}: {res.text[:300]}"


def test_run_auto_paper_intraday_loop_not_in_allowlist() -> None:
    assert "run-auto-paper-intraday-loop" not in ALLOWED_COMMANDS
    assert is_forbidden("run-auto-paper-intraday-loop")


def test_unsafe_command_names_rejected() -> None:
    for cmd in ("place-order", "run-auto-paper-mtf-loop", "telegram-listen"):
        ok, _ = validate_request(CommandRequest(command=cmd, args=()))
        assert ok is False


def test_paper_readonly_broker_views_are_allowlisted() -> None:
    for cmd in ("open-orders", "portfolio", "paper-daily-report", "paper-weekly-report"):
        assert cmd in ALLOWED_COMMANDS


def test_signals_ict_page_states_execution_invariants(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    r = _client(tmp_path).get("/signals?strategy=ict_smc_intraday_v1")
    assert r.status_code == 200
    t = r.text
    assert "1-minute" in t
    # Human copy: news + edge are context, not a trade signal.
    assert "place trades" in t.lower() and "edge" in t.lower()


def test_edge_page_states_edge_cannot_trigger_trade_alone(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    r = _client(tmp_path).get("/edge")
    assert r.status_code == 200
    assert "Edge cannot trigger" in r.text
