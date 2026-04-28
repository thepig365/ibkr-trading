"""Dashboard flash banner from last allowlisted command result."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.i18n import t as translate_i18n
from bot_ui.routes._helpers import dashboard_flash_from_recent_command
from bot_ui.services.command_queue import CommandRequest, CommandResult, LocalCommandRunner
from bot_ui.services.state_store import LocalFileStateStore


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_dashboard_flash_trade_charts_summary_parses_json(project_root: Path) -> None:
    """Simulate stdout from complete-trade-charts --json (MODE line + JSON)."""
    stdout = (
        "MODE: demo\n"
        + '{"generated_count": 0, "available_count": 3, "missing_candles_count": 0, '
        '"error_count": 0, "no_exit_count": 2, "skipped_status_count": 1, '
        '"would_generate_count": 0}\n'
    )
    r = CommandResult(
        request=CommandRequest(
            command="complete-trade-charts",
            args=(
                "--latest",
                "--limit",
                "50",
                "--fetch-missing-candles",
                "--window-before-minutes",
                "30",
                "--window-after-minutes",
                "90",
                "--json",
            ),
        ),
        accepted=True,
        exit_code=0,
        stdout=stdout,
        started_utc="2026-04-29T00:00:00+00:00",
        finished_utc="2026-04-29T00:00:01+00:00",
        duration_seconds=0.2,
    )

    def _t(key: str, **kw: str | float) -> str:
        return translate_i18n(key, "en", **kw)

    banner = dashboard_flash_from_recent_command(r, t=_t, max_age_seconds=None)
    assert banner is not None
    assert banner["kind"] == "ok"
    assert "0" in banner["message"]
    assert "eligible" in banner["message"].lower() or "eligible" in banner["message"]


def test_dashboard_get_shows_flash_after_post_complete_charts(project_root: Path, tmp_path: Path) -> None:
    """End-to-end: POST runs real CLI in repo → GET /dashboard picks up flash."""
    (tmp_path / "data").mkdir(parents=True)
    state = LocalFileStateStore(tmp_path)
    queue = LocalCommandRunner(
        project_root=project_root,
        python_executable=sys.executable,
        timeout_seconds=120,
        audit_file=tmp_path / "audit.jsonl",
    )
    client = TestClient(create_app(project_root=project_root, state_store=state, command_queue=queue))
    args = "--latest --limit 2 --fetch-missing-candles --window-before-minutes 30 --window-after-minutes 90 --json"
    r = client.post(
        "/api/commands/run",
        data={"command": "complete-trade-charts", "args": args, "return_to": "/dashboard"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    body = client.get("/dashboard").text
    assert 'class="flash ok"' in body
    assert (
        "Ran complete-trade-charts" in body
        or "Charts generated" in body
        or "Charts generated：" in body
        or "本次生成 PNG" in body
    )
