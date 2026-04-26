"""UI: auto loop readiness panel (read-only; no run-auto-paper-intraday-loop in UI)."""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import CommandRequest, LocalCommandRunner, validate_request
from bot_ui.services.safety import ALLOWED_COMMANDS, validate_args_for
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent


def _install_min_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    for n in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        s = REPO / "config" / n
        if s.is_file():
            shutil.copy(s, root / "config" / n)


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    _install_min_config(root)
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=20,
        audit_file=root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def test_ui_shows_automatic_paper_loop_readiness_blocks(tmp_path: Path) -> None:
    c = _client(tmp_path)
    d = c.get("/dashboard")
    assert d.status_code == 200
    t = d.text
    for chunk in (
        "Automatic paper loop readiness",
        "Check Auto Loop Readiness",
        'value="auto-loop-readiness"',
    ):
        assert chunk in t, chunk

    p = c.get("/paper")
    assert p.status_code == 200
    pt = p.text
    for chunk in (
        "Automatic paper loop readiness",
        "Check Auto Loop Readiness",
    ):
        assert chunk in pt, chunk


def test_ui_does_not_expose_run_auto_paper_intraday_loop(tmp_path: Path) -> None:
    c = _client(tmp_path)
    for path in ("/dashboard", "/paper"):
        t = c.get(path).text
        assert "run-auto-paper-intraday-loop" not in t


def test_auto_loop_readiness_in_allowlist_and_forbidden_unchanged() -> None:
    assert "auto-loop-readiness" in ALLOWED_COMMANDS
    assert "run-auto-paper-intraday-loop" not in ALLOWED_COMMANDS


def test_validate_args_auto_loop_readiness() -> None:
    ok, err = validate_args_for("auto-loop-readiness", ())
    assert ok, err
    assert validate_args_for("auto-loop-readiness", ("--json",))[0] is True
    assert validate_args_for("auto-loop-readiness", ("--probe-ibkr",))[0] is True
    bad, reason = validate_args_for("auto-loop-readiness", ("--foo",))
    assert not bad
    assert "foo" in reason or "only" in reason.lower()


def test_command_runner_accepts_auto_loop_readiness() -> None:
    ok, reason = validate_request(
        CommandRequest(command="auto-loop-readiness", args=("--json",))
    )
    assert ok is True, reason
