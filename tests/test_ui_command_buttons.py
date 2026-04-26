"""POST /api/commands/run: safe allowlisted commands, redirects, rejections (no real paper passes)."""

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
from bot_ui.services.state_store import LocalFileStateStore

REPO = Path(__file__).resolve().parent.parent

# Real repo layout so `python -m bot.cli engine-status` and peers exit 0 in tests.
_FAKE_AUDIT = REPO / "data" / "runtime" / "ui_cmd_button_test.jsonl"


def _client(root: Path) -> TestClient:
    state = LocalFileStateStore(root)
    queue = LocalCommandRunner(
        project_root=root,
        python_executable=sys.executable,
        timeout_seconds=120,
        audit_file=_FAKE_AUDIT,
    )
    return TestClient(
        create_app(project_root=root, state_store=state, command_queue=queue)
    )


def _last_result(c: TestClient):
    return c.app.state.command_queue.list_recent(1)[0]


@pytest.fixture
def ui_client() -> TestClient:
    return _client(REPO)


def test_post_engine_status_accepted_and_page_200(ui_client: TestClient) -> None:
    r = ui_client.post(
        "/api/commands/run",
        data={"command": "engine-status", "return_to": "/dashboard"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    lr = _last_result(ui_client)
    assert lr.accepted
    assert lr.exit_code == 0
    assert "Strategy Lab" in r.text


def test_post_research_status_and_macro_calendar(ui_client: TestClient) -> None:
    for cmd, args, ret in (
        ("research-status", "", "/research"),
        ("macro-calendar", "--today", "/research"),
    ):
        r = ui_client.post(
            "/api/commands/run",
            data={"command": cmd, "args": args, "return_to": ret},
            follow_redirects=True,
        )
        assert r.status_code == 200, (cmd, r.text[:200])
        lr = _last_result(ui_client)
        assert lr.accepted
        assert lr.exit_code == 0


def test_post_report_and_edge_path_commands(ui_client: TestClient) -> None:
    for cmd, args in (
        ("edge-profile-report", "--latest"),
        ("paper-daily-report", "--latest"),
        ("paper-weekly-report", "--latest"),
        ("backtest-report", "--latest"),
    ):
        r = ui_client.post(
            "/api/commands/run",
            data={"command": cmd, "args": args, "return_to": "/reports"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        lr = _last_result(ui_client)
        assert lr.accepted, (cmd, lr.rejected_reason)
        assert lr.exit_code is not None
        assert lr.exit_code in {0, 1}


def test_post_paper_activation_and_intraday_status(ui_client: TestClient) -> None:
    for cmd in ("paper-activation-status", "intraday-paper-status"):
        r = ui_client.post(
            "/api/commands/run",
            data={"command": cmd, "return_to": "/paper"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert _last_result(ui_client).accepted
        assert _last_result(ui_client).exit_code == 0


def test_intraday_paper_on_off_is_allowlisted_without_runtime_toggle_test() -> None:
    """Do not POST on/off in smoke tests (filesystem side effects). Validate only."""
    for cmd in ("intraday-paper-on", "intraday-paper-off"):
        ok, reason = validate_request(CommandRequest(command=cmd, args=()))
        assert ok, reason


def test_post_settings_data_status_and_cleanup_dry_run(ui_client: TestClient) -> None:
    for cmd, args in (("data-status", ""), ("data-cleanup", "--dry-run")):
        r = ui_client.post(
            "/api/commands/run",
            data={
                "command": cmd,
                "args": args,
                "return_to": "/settings",
            },
            follow_redirects=True,
        )
        assert r.status_code == 200, (cmd, r.text[:200])
        assert _last_result(ui_client).accepted


def test_post_watchlist_build_watchlist(ui_client: TestClient) -> None:
    r = ui_client.post(
        "/api/commands/run",
        data={
            "command": "build-watchlist",
            "args": "--limit 30",
            "return_to": "/watchlist",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert _last_result(ui_client).accepted


def test_post_strategies_strategy_list_json(ui_client: TestClient) -> None:
    r = ui_client.post(
        "/api/commands/run",
        data={
            "command": "strategy-list",
            "args": "--json",
            "return_to": "/strategies",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert _last_result(ui_client).accepted


def test_multi_strategy_scan_is_allowlisted_for_strategies_page() -> None:
    """Full multi-strategy-scan can take minutes; validate args only (matches Strategies button)."""
    ok, err = validate_request(
        CommandRequest(command="multi-strategy-scan", args=tuple())
    )
    assert ok, err


def test_post_signals_scan_validates_same_args_as_quick_button() -> None:
    ok, err = validate_request(
        CommandRequest(
            command="scan-intraday-smc-watchlist",
            args=(
                "--source",
                "dynamic",
                "--limit",
                "10",
                "--ibkr",
            ),
        )
    )
    assert ok, err


def test_paper_readiness_check_validates_for_dashboard_button() -> None:
    """Same args as Readiness on Dashboard; validate only (full run can probe/scan)."""
    ok, err = validate_request(
        CommandRequest(
            command="paper-readiness-check",
            args=("--intraday", "--source", "dynamic", "--limit", "20"),
        )
    )
    assert ok, err


def test_broker_readonly_commands_validate_for_ui_buttons() -> None:
    """Allowlist/validator only — avoids blocking on TWS in CI (operators test from live UI)."""
    for cmd in ("ibkr-session-status", "open-orders", "portfolio", "paper-reconcile"):
        ok, err = validate_request(CommandRequest(command=cmd, args=()))
        assert ok, f"{cmd}: {err}"


def test_forbidden_command_rejected_by_runner(ui_client: TestClient) -> None:
    ui_client.post(
        "/api/commands/run",
        data={
            "command": "run-auto-paper-intraday-loop",
            "args": "--source dynamic --limit 1",
            "return_to": "/paper",
        },
        follow_redirects=True,
    )
    lr = _last_result(ui_client)
    assert lr.accepted is False


def test_unsafe_arg_rejected(ui_client: TestClient) -> None:
    ui_client.post(
        "/api/commands/run",
        data={"command": "engine-status", "args": "--live", "return_to": "/dashboard"},
        follow_redirects=True,
    )
    lr = _last_result(ui_client)
    assert lr.accepted is False


def test_backtest_page_quick_forms_post_validate_only(ui_client: TestClient) -> None:
    r = ui_client.post(
        "/api/commands/run",
        data={
            "command": "backtest-report",
            "args": "--latest",
            "return_to": "/backtest",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert _last_result(ui_client).accepted


def test_controlled_paper_clis_not_in_automated_post_tests() -> None:
    """Never POST the controlled one-shot paper CLIs from this file (string built in-test)."""
    text = Path(__file__).read_text(encoding="utf-8")
    fpp = "first" + "-paper-" + "pass"
    aps = "auto" + "-paper-intraday-smc"
    assert f"\"command\": \"{fpp}\"" not in text
    assert f"\"command\": \"{aps}\"" not in text


def test_research_page_buttons_post(ui_client: TestClient) -> None:
    r = ui_client.post(
        "/api/commands/run",
        data={"command": "ibkr-news-status", "return_to": "/research"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert _last_result(ui_client).accepted
