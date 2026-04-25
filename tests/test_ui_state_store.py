"""Tests for the local Strategy Lab state store.

These tests assemble synthetic project layouts in tmp_path so they
don't depend on any real broker data. They verify that:

* Missing files never raise; accessors return typed empty objects.
* Account snapshot / positions / watchlist / signals / loop status are
  parsed correctly when files are present.
* The kill switch / mtf-auto runtime flags are read from
  ``data/runtime/`` files exactly as the auto-paper loop writes them.
* Reading log files outside the project root is refused.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bot_ui.services.state_store import (
    KILL_SWITCH_FILE,
    LOOP_STATE_FILE,
    MTF_AUTO_PAPER_ENABLED_FILE,
    DatabaseStateStore,
    LocalFileStateStore,
    get_state_store,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_positions_db(sqlite_path: Path, *, ts: str, rows: list[tuple[str, float, float | None]]) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(sqlite_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions_snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_utc TEXT NOT NULL,
              account_id TEXT NOT NULL,
              symbol TEXT NOT NULL,
              sec_type TEXT,
              exchange TEXT,
              currency TEXT,
              position REAL NOT NULL,
              avg_cost REAL
            )
            """
        )
        for sym, qty, cost in rows:
            conn.execute(
                "INSERT INTO positions_snapshots (ts_utc, account_id, symbol, position, avg_cost) "
                "VALUES (?,?,?,?,?)",
                (ts, "DUTEST", sym, qty, cost),
            )


def test_empty_project_returns_empty_views(tmp_path: Path) -> None:
    store = LocalFileStateStore(tmp_path)
    assert store.account_summary().is_empty
    assert store.positions() == []
    assert store.watchlist().is_empty
    assert store.signals().is_empty
    assert store.loop_status().is_empty
    flags = store.runtime_flags()
    assert flags.kill_switch_active is False
    assert flags.mtf_auto_paper_enabled is False
    assert flags.mtf_auto_paper_explicit_off is False
    assert store.list_log_files() == []


def test_account_summary_picks_latest_real_account_id(tmp_path: Path) -> None:
    snap_path = tmp_path / "data" / "account_snapshots.jsonl"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts_utc": "2026-04-24T10:00:00Z", "account_id": "DUOLD", "net_liquidation": 999.0,
         "total_cash": 100.0, "buying_power": 200.0, "available_funds": 300.0, "currency": "AUD"},
        {"ts_utc": "2026-04-24T15:00:00Z", "account_id": "DUTEST", "net_liquidation": 1234.5,
         "total_cash": 100.0, "buying_power": 200.0, "available_funds": 300.0, "currency": "AUD"},
        {"ts_utc": "2026-04-24T15:00:01Z", "account_id": "All", "net_liquidation": None,
         "total_cash": None, "buying_power": None, "available_funds": None, "currency": "AUD"},
    ]
    snap_path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s = LocalFileStateStore(tmp_path).account_summary()
    assert s.account_id == "DUTEST"
    assert s.net_liquidation == 1234.5
    assert s.snapshot_ts_utc == "2026-04-24T15:00:00Z"


def test_positions_returns_only_nonzero_from_latest_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "data" / "trading_bot.sqlite"
    _make_positions_db(
        db,
        ts="2026-04-24T15:00:00Z",
        rows=[("AAPL", 10.0, 175.0), ("FLAT", 0.0, 1.0), ("__ACCOUNT_NO_POSITIONS__", 0.0, None)],
    )
    # Insert an older snapshot with different symbols too, must be ignored
    _make_positions_db(db, ts="2026-04-23T15:00:00Z", rows=[("OLD", 5.0, 10.0)])
    rows = LocalFileStateStore(tmp_path).positions()
    assert [r.symbol for r in rows] == ["AAPL"]
    assert rows[0].position == 10.0


def test_watchlist_parses_dynamic_json(tmp_path: Path) -> None:
    p = tmp_path / "data" / "watchlists" / "2026-04-24-dynamic-watchlist.json"
    payload = {
        "date": "2026-04-24",
        "source": "ibkr",
        "symbols": [
            {"symbol": "NVDA", "reason": ["high_avg_dollar_volume", "static_core"],
             "latest_price": 208.88, "relative_volume": 1.35, "blocked": False},
            {"symbol": "AAPL", "reason": "static_core", "blocked": True},
            "MSFT",  # plain string fallback
            {"symbol": "", "blocked": False},  # filtered
        ],
    }
    _write(p, json.dumps(payload))
    v = LocalFileStateStore(tmp_path).watchlist()
    assert not v.is_empty
    assert v.date == "2026-04-24"
    assert v.source == "ibkr"
    syms = [s.symbol for s in v.symbols]
    assert syms == ["NVDA", "AAPL", "MSFT"]
    assert v.symbols[0].reason == ["high_avg_dollar_volume", "static_core"]
    assert v.symbols[1].blocked is True
    assert v.file_path is not None and v.file_path.endswith("2026-04-24-dynamic-watchlist.json")


def test_signals_parses_summary_json(tmp_path: Path) -> None:
    p = tmp_path / "data" / "mtf_smc" / "2026-04-24-watchlist-mtf-smc-summary.json"
    payload = {
        "date": "2026-04-24",
        "source": "dynamic",
        "symbols_scanned": 3,
        "counts": {"FULL_ALIGNMENT": 1, "BLOCKED": 1, "BIAS_OK_SETUP_INCOMPLETE": 1},
        "top_by_alignment_score": [
            {"symbol": "NVDA", "mtf_alignment_score": 80,
             "alignment_category": "FULL_ALIGNMENT",
             "eligible_for_future_paper_trade": True},
            {"symbol": "AMD", "mtf_alignment_score": 40,
             "alignment_category": "BIAS_OK_SETUP_INCOMPLETE",
             "eligible_for_future_paper_trade": False},
        ],
        "eligible_for_future_paper_trade": ["NVDA"],
    }
    _write(p, json.dumps(payload))
    v = LocalFileStateStore(tmp_path).signals()
    assert v.symbols_scanned == 3
    assert v.counts["FULL_ALIGNMENT"] == 1
    assert v.top[0].symbol == "NVDA"
    assert v.top[0].eligible_for_future_paper_trade is True
    assert v.eligible == ["NVDA"]


def test_loop_status_prefers_state_json(tmp_path: Path) -> None:
    state = {
        "last_cycle_utc": "2026-04-24T19:28:41.895727+00:00",
        "last_status": "success",
        "last_reason": "",
        "last_full_alignment_count": 0,
        "last_orders_submitted": 0,
        "kill_switch": False,
        "runtime_mtf_on": True,
        "runtime_mtf_off_explicit": False,
        "cycles": 49,
        "last_heartbeat_ts": 1777057584.5533109,
    }
    _write(tmp_path / "data" / "runtime" / LOOP_STATE_FILE, json.dumps(state))
    s = LocalFileStateStore(tmp_path).loop_status()
    assert s.cycles == 49
    assert s.runtime_mtf_on is True
    assert s.last_status == "success"
    assert s.last_heartbeat_ts == 1777057584.5533109


def test_loop_status_falls_back_to_jsonl_tail(tmp_path: Path) -> None:
    p = tmp_path / "data" / "auto_paper_loop" / "2026-04-24-loop.jsonl"
    lines = [
        json.dumps({"timestamp": "2026-04-24T14:31:41Z", "cycle": 1, "status": "success",
                    "full_alignment_count": 0, "orders_submitted": 0,
                    "kill_switch": False, "runtime_mtf_on": True,
                    "runtime_mtf_off_explicit": False}),
        json.dumps({"timestamp": "2026-04-24T14:41:41Z", "cycle": 2, "status": "success",
                    "full_alignment_count": 1, "orders_submitted": 0,
                    "kill_switch": False, "runtime_mtf_on": True,
                    "runtime_mtf_off_explicit": False}),
    ]
    _write(p, "\n".join(lines) + "\n")
    s = LocalFileStateStore(tmp_path).loop_status()
    assert s.cycles == 2
    assert s.last_full_alignment_count == 1
    assert s.last_status == "success"


def test_runtime_flags_kill_switch_and_mtf(tmp_path: Path) -> None:
    runtime = tmp_path / "data" / "runtime"
    _write(runtime / KILL_SWITCH_FILE, "1\n")
    _write(runtime / MTF_AUTO_PAPER_ENABLED_FILE, "1\n")
    flags = LocalFileStateStore(tmp_path).runtime_flags()
    assert flags.kill_switch_active is True
    assert flags.mtf_auto_paper_enabled is True
    assert flags.mtf_auto_paper_explicit_off is False

    (runtime / MTF_AUTO_PAPER_ENABLED_FILE).write_text("0\n", encoding="utf-8")
    flags2 = LocalFileStateStore(tmp_path).runtime_flags()
    assert flags2.mtf_auto_paper_enabled is False
    assert flags2.mtf_auto_paper_explicit_off is True


def test_safety_view_flags_non_paper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_ACCOUNT_MODE", "live")
    s = LocalFileStateStore(tmp_path).safety_view()
    assert s.paper_only is True
    assert s.block_live_trading is True
    assert s.ibkr_account_mode_env == "live"
    assert s.issues  # non-empty: live env raises a warning

    monkeypatch.setenv("IBKR_ACCOUNT_MODE", "paper")
    s2 = LocalFileStateStore(tmp_path).safety_view()
    assert s2.issues == []


def test_tail_file_refuses_outside_project_root(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    other = tmp_path_factory.mktemp("outside")
    target = other / "secret.log"
    target.write_text("should never be read\n", encoding="utf-8")
    store = LocalFileStateStore(tmp_path)
    assert store.tail_file(target) == ""


def test_list_log_files_finds_logs_and_jsonl(tmp_path: Path) -> None:
    _write(tmp_path / "logs" / "auto_paper.log", "hello\n")
    _write(tmp_path / "data" / "auto_paper_loop" / "2026-04-24-loop.jsonl", "{}\n")
    files = LocalFileStateStore(tmp_path).list_log_files()
    rels = {p.name for p in files}
    assert "auto_paper.log" in rels
    assert "2026-04-24-loop.jsonl" in rels


def test_database_state_store_is_placeholder() -> None:
    db = DatabaseStateStore("postgres://nope")
    with pytest.raises(NotImplementedError):
        db.account_summary()


def test_factory_local_returns_local_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_LAB_BACKEND", "local")
    s = get_state_store(tmp_path)
    assert isinstance(s, LocalFileStateStore)


def test_factory_remote_returns_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_LAB_BACKEND", "remote")
    s = get_state_store(tmp_path)
    assert isinstance(s, DatabaseStateStore)


def test_factory_unknown_backend_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRATEGY_LAB_BACKEND", "weird")
    with pytest.raises(ValueError):
        get_state_store(tmp_path)
