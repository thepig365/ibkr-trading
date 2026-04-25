"""Tests for the /strategies UI page (Prompt 13C PART H/I/J).

Hard requirements verified here:

* Rendering /strategies never imports ``bot.ibkr_client`` /
  ``bot.broker`` / ``ib_async``. The page reads JSON files only.
* The page returns 200 on an empty project.
* When per-strategy or multi-strategy scan files exist, the page
  surfaces them via the state store summary.
* The state store's ``get_strategy_registry_summary()`` handles
  missing files / missing config gracefully.
* The command runner allowlist accepts strategy commands and rejects
  invalid ``--strategy`` payloads.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_ui.app import create_app
from bot_ui.services.command_queue import (
    CommandRequest,
    LocalCommandRunner,
    validate_request,
)
from bot_ui.services.state_store import (
    LocalFileStateStore,
    StrategyRegistryEntry,
    StrategyRegistrySummary,
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
        timeout_seconds=15,
        audit_file=project_root / "ui_audit.jsonl",
    )
    app = create_app(project_root=project_root, state_store=state, command_queue=queue)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Page renders without IBKR import
# ---------------------------------------------------------------------------


def test_strategies_page_renders_200_with_empty_project(project: Path) -> None:
    r = _client(project).get("/strategies")
    assert r.status_code == 200, r.text
    # The page heading + sidebar both display "Strategies".
    assert "Strategies" in r.text
    # Engine invariants block must render.
    assert "Engine invariants" in r.text
    assert "paper-only" in r.text


def test_strategies_page_does_not_import_broker_or_ibkr_client(
    project: Path,
) -> None:
    """Critical safety guarantee: rendering /strategies must NOT load
    bot.broker / bot.ibkr_client / ib_async into sys.modules."""
    # Snapshot which broker/IBKR modules are already loaded by other
    # tests, then ensure rendering /strategies doesn't add new ones.
    pre_banned = {
        m
        for m in sys.modules
        if m == "bot.broker" or m == "bot.ibkr_client" or m == "ib_async"
    }
    client = _client(project)
    r = client.get("/strategies")
    assert r.status_code == 200
    post_banned = {
        m
        for m in sys.modules
        if m == "bot.broker" or m == "bot.ibkr_client" or m == "ib_async"
    }
    new_banned = post_banned - pre_banned
    assert not new_banned, (
        f"Rendering /strategies pulled in banned modules: {sorted(new_banned)}"
    )


def test_strategies_page_lists_all_registered_strategies(project: Path) -> None:
    r = _client(project).get("/strategies")
    assert r.status_code == 200
    for key in (
        "mtf_smc",
        "ict_smc_intraday_v1",
        "chanlun_intraday_v1",
        "orb_baseline",
    ):
        assert key in r.text, f"missing strategy {key!r} on /strategies"


def test_strategies_page_buttons_use_command_runner(project: Path) -> None:
    """Buttons should POST to the allowlisted command runner endpoint."""
    r = _client(project).get("/strategies")
    assert "/api/commands/run" in r.text
    assert "multi-strategy-scan" in r.text
    assert "strategy-status" in r.text
    assert "strategy-list" in r.text


# ---------------------------------------------------------------------------
# State store summary
# ---------------------------------------------------------------------------


def test_state_store_get_strategy_registry_summary_empty_project(
    project: Path,
) -> None:
    store = LocalFileStateStore(project)
    summary = store.get_strategy_registry_summary()
    assert isinstance(summary, StrategyRegistrySummary)
    assert summary.paper_only is True
    assert summary.paper_execution_allowed is False
    keys = [e.key for e in summary.strategies]
    assert "mtf_smc" in keys
    # All scans are stale on an empty project.
    for e in summary.strategies:
        assert e.is_stale is True
    assert summary.multi_scan_path is None
    assert summary.multi_scan_is_stale is True


def test_state_store_picks_up_multi_strategy_scan_file(project: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sd = project / "data" / "strategies"
    sd.mkdir(parents=True)
    payload = {
        "started_utc": f"{today}T00:00:00Z",
        "finished_utc": f"{today}T00:00:01Z",
        "enabled_keys": ["mtf_smc"],
        "skipped_keys": ["chanlun_intraday_v1"],
        "requested_keys": ["mtf_smc"],
        "results": [],
        "total_signals": 4,
        "paper_only": True,
        "execution_allowed": False,
    }
    (sd / f"{today}-multi-strategy-scan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    store = LocalFileStateStore(project)
    summary = store.get_strategy_registry_summary()
    assert summary.multi_scan_path is not None
    assert summary.multi_scan_total_signals == 4
    assert summary.multi_scan_enabled_keys == ["mtf_smc"]
    assert summary.multi_scan_is_stale is False
    assert summary.multi_scan_date == today


def test_state_store_picks_up_per_strategy_scan_file(project: Path) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sd = project / "data" / "strategies"
    sd.mkdir(parents=True)
    payload = {
        "strategy_key": "mtf_smc",
        "started_utc": f"{today}T00:00:00Z",
        "finished_utc": f"{today}T00:00:01Z",
        "status": "ok",
        "symbol_count": 5,
        "signal_count": 3,
        "signals": [],
        "summary": {},
        "notes": [],
        "execution_allowed": False,
        "paper_only": True,
    }
    (sd / f"{today}-mtf_smc-scan.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    store = LocalFileStateStore(project)
    summary = store.get_strategy_registry_summary()
    by_key = {e.key: e for e in summary.strategies}
    mtf = by_key["mtf_smc"]
    assert mtf.latest_scan_status == "ok"
    assert mtf.latest_signal_count == 3
    assert mtf.is_stale is False
    assert mtf.latest_scan_date == today


def test_state_store_strategy_summary_marks_old_scans_as_stale(project: Path) -> None:
    sd = project / "data" / "strategies"
    sd.mkdir(parents=True)
    (sd / "2020-01-01-mtf_smc-scan.json").write_text(
        json.dumps({"strategy_key": "mtf_smc", "status": "ok", "signal_count": 0}),
        encoding="utf-8",
    )
    store = LocalFileStateStore(project)
    summary = store.get_strategy_registry_summary()
    by_key = {e.key: e for e in summary.strategies}
    assert by_key["mtf_smc"].is_stale is True
    assert by_key["mtf_smc"].latest_scan_date == "2020-01-01"


# ---------------------------------------------------------------------------
# Command runner allowlist
# ---------------------------------------------------------------------------


def test_command_runner_accepts_strategy_commands() -> None:
    for cmd, args in [
        ("strategy-list", ()),
        ("strategy-list", ("--json",)),
        ("strategy-info", ("mtf_smc",)),
        ("strategy-status", ()),
        ("strategy-status", ("--json",)),
        ("strategy-scan", ("--strategy", "mtf_smc")),
        ("strategy-scan", ("--strategy", "mtf_smc", "--json")),
        ("multi-strategy-scan", ()),
        ("multi-strategy-scan", ("--include-disabled",)),
    ]:
        ok, reason = validate_request(CommandRequest(command=cmd, args=args))
        assert ok, f"{cmd} {args} should be accepted but was rejected: {reason}"


def test_command_runner_rejects_invalid_strategy_keys() -> None:
    # "MTF-SMC" -> uppercase + dash; "../" path traversal; "1abc" leading
    # digit; "x"*40 too long; "abc;rm" shell metachar; "" empty.
    for bad in ("MTF-SMC", "../etc/passwd", "1abc", "x" * 40, "abc;rm", ""):
        ok, reason = validate_request(
            CommandRequest(command="strategy-scan", args=("--strategy", bad))
        )
        assert ok is False, f"strategy-scan --strategy {bad!r} should be rejected"
        assert reason


def test_command_runner_rejects_strategy_scan_without_strategy_flag() -> None:
    ok, reason = validate_request(CommandRequest(command="strategy-scan"))
    assert ok is False
    assert "--strategy" in reason


def test_command_runner_rejects_strategy_info_without_key() -> None:
    ok, reason = validate_request(CommandRequest(command="strategy-info"))
    assert ok is False
    ok, reason = validate_request(CommandRequest(command="strategy-info", args=("a", "b")))
    assert ok is False


def test_command_runner_still_blocks_live_trading_after_13c() -> None:
    """Adding strategy commands must NOT loosen the live-trading deny-list."""
    for bad in (
        "auto-paper-mtf",
        "run-auto-paper-mtf-loop",
        "telegram-listen",
        "place-order",
    ):
        ok, _ = validate_request(CommandRequest(command=bad))
        assert ok is False, f"{bad!r} must remain forbidden"


def test_strategies_page_button_post_redirects_back(project: Path) -> None:
    """Posting strategy-status from /strategies must redirect to /strategies."""
    client = _client(project)
    r = client.post(
        "/api/commands/run",
        data={"command": "strategy-status", "args": "--json", "return_to": "/strategies"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/strategies"


def test_strategy_registry_entry_defaults_are_safe() -> None:
    e = StrategyRegistryEntry(key="x")
    assert e.enabled is False
    assert e.requires_ibkr is True
    assert e.is_stale is True
    assert e.latest_signal_count == 0
