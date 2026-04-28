"""Watchlist page: criteria / price disclaimers — no broker on GET."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.routes import watchlist_helpers
from bot_ui.services.command_queue import (
    CommandRequest,
    CommandResult,
    LocalCommandRunner,
    validate_request,
)
from bot_ui.services.state_store import LocalFileStateStore


def _client(root: Path) -> TestClient:
    (root / "data").mkdir(exist_ok=True)
    wl = root / "data" / "watchlists"
    wl.mkdir(parents=True, exist_ok=True)
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


def test_watchlist_returns_200(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist")
    assert r.status_code == 200


def test_watchlist_explains_prices_not_on_load(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist")
    assert r.status_code == 200
    assert "Prices are not fetched on page load" in r.text


def test_watchlist_explains_static_core_reason(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist")
    assert r.status_code == 200
    assert "static_core" in r.text


def test_watchlist_zh_has_chinese_explainer(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist?lang=zh")
    assert r.status_code == 200
    assert "本页含义" in r.text
    assert "不会拉价" in r.text


def test_watchlist_shows_disk_line(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist")
    assert r.status_code == 200
    assert "File on disk" in r.text


def test_recent_hint_offline_notes_no_ibkr_prices() -> None:
    res = CommandResult(
        request=CommandRequest(command="build-watchlist", args=("--limit", "30")),
        accepted=True,
        exit_code=0,
        stdout="Saved: data/watchlists/2026-04-01-dynamic-watchlist.json\n",
    )
    hints = watchlist_helpers.recent_command_hints_for_watchlist([res])
    assert hints[0]
    assert "`--ibkr`" in hints[0] or "offline" in hints[0].lower()


def test_recent_hint_ibkr_expects_daily_bars_note() -> None:
    res = CommandResult(
        request=CommandRequest(
            command="build-watchlist",
            args=("--ibkr", "--limit", "50"),
        ),
        accepted=True,
        exit_code=0,
        stdout="Saved: data/watchlists/2026-04-01-dynamic-watchlist.json\n",
    )
    hints = watchlist_helpers.recent_command_hints_for_watchlist([res])
    assert "Saved:" in hints[0]


def test_ui_get_watchlist_route_no_ibkr_import(tmp_project: Path) -> None:
    import bot_ui.routes.watchlist as wl_mod

    src = Path(wl_mod.__file__).read_text(encoding="utf-8")
    assert "bot.ibkr" not in src
    assert _client(tmp_project).get("/watchlist").status_code == 200


def test_no_place_order_on_watchlist_page(tmp_project: Path) -> None:
    r = _client(tmp_project).get("/watchlist")
    assert "place-order" not in r.text.lower()


def test_build_watchlist_ibkr_args_are_allowlisted() -> None:
    ok, err = validate_request(
        CommandRequest(
            command="build-watchlist",
            args=("--ibkr", "--limit", "50"),
        )
    )
    assert ok, err
