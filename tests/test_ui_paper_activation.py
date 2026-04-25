"""UI: Paper page local activation + allowlist (Prompt 13I)."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import LocalCommandRunner
from bot_ui.services.safety import ALLOWED_COMMANDS, validate_args_for
from bot_ui.services.state_store import LocalFileStateStore

NEW_CMDS: tuple[str, ...] = (
    "paper-activation-status",
    "write-paper-local-config",
    "intraday-paper-on",
    "intraday-paper-off",
    "paper-readiness-check",
    "first-paper-pass",
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
        timeout_seconds=25,
        audit_file=project_root / "ui_audit.jsonl",
    )
    return TestClient(
        create_app(project_root=project_root, state_store=state, command_queue=queue)
    )


def test_paper_page_200_includes_local_activation(project: Path) -> None:
    r = _client(project).get("/paper")
    assert r.status_code == 200
    assert "Local Paper Activation" in r.text
    assert "write-paper-local-config" in r.text
    assert "first-paper-pass" in r.text


def test_paper_page_render_no_broker_ip(project: Path) -> None:
    import sys as _sys
    from typing import Any

    removed: dict[str, Any] = {}
    for key in list(_sys.modules):
        if key in {"bot.broker", "bot.ibkr_client"} or key.startswith("ib_async"):
            removed[key] = _sys.modules.pop(key, None)
    try:
        r = _client(project).get("/paper")
        assert r.status_code == 200
        assert "bot.broker" not in _sys.modules
    finally:
        _sys.modules.update({k: v for k, v in removed.items() if v is not None})


def test_new_paper_commands_allowlisted() -> None:
    for c in NEW_CMDS:
        assert c in ALLOWED_COMMANDS, c


def test_write_local_config_args() -> None:
    assert validate_args_for("write-paper-local-config", ()) == (True, "")
    assert validate_args_for("write-paper-local-config", ("--write",)) == (True, "")
    ok, _ = validate_args_for("write-paper-local-config", ("--foo",))
    assert ok is False


def test_paper_readiness_check_args() -> None:
    ok, _ = validate_args_for(
        "paper-readiness-check",
        ("--intraday", "--probe-ibkr", "--scan", "--source", "dynamic", "--limit", "20"),
    )
    assert ok


def test_first_paper_pass_args() -> None:
    ok, err = validate_args_for(
        "first-paper-pass", ("--source", "static", "--limit", "5", "--telegram")
    )
    assert ok, err
