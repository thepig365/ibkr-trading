"""Broker snapshot helpers (lazy IBKR deps; filesystem cache only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.broker_snapshot import (
    _account_metrics_from_summaries,
    broker_snapshot_last_path,
    infer_symbol_broker_state,
    load_broker_snapshot,
    refresh_broker_snapshot_best_effort,
)
from bot.config import load_config
from bot.ibkr_connection import IbkrRoConnectOutcome


def test_load_broker_snapshot_missing(tmp_project: Path) -> None:
    assert load_broker_snapshot(tmp_project) is None


def test_refresh_unavailable_writes_snapshot_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_project: Path
) -> None:
    """TWS unreachable path must still persist an envelope."""

    captured: dict[str, object] = {}

    def _fake(cfg, roster_key: str) -> IbkrRoConnectOutcome:  # type: ignore[no-untyped-def]
        captured["roster"] = roster_key
        return IbkrRoConnectOutcome(
            client=None,
            client_id_used=None,
            attempted_client_ids=[2],
            fatal_message="simulated unreachable",
            live_blocked=None,
        )

    monkeypatch.setattr(
        "bot.ibkr_connection.connect_readonly_roster_retry",
        _fake,
    )

    cfg = load_config(project_root=tmp_project)
    snap = refresh_broker_snapshot_best_effort(cfg=cfg)
    assert snap.status == "unavailable"
    snap_path = broker_snapshot_last_path(tmp_project)
    assert snap_path.is_file()
    raw = json.loads(snap_path.read_text(encoding="utf-8"))
    assert raw.get("status") == "unavailable"
    assert captured.get("roster") == "broker_readonly"


def test_infer_symbol_broker_states() -> None:
    assert infer_symbol_broker_state("SPY", None) == "not_checked"
    snap_err = {"status": "error"}
    assert infer_symbol_broker_state("SPY", snap_err).startswith("broker_")
    snap_unavail = {"status": "unavailable"}
    assert infer_symbol_broker_state("SPY", snap_unavail) == "broker_unavailable"
    snap_ok_flat = {"status": "ok", "positions": [], "open_orders": []}
    assert infer_symbol_broker_state("SPY", snap_ok_flat) == "flat_no_position"
    snap_ok_pos = {
        "status": "ok",
        "positions": [{"symbol": "spy", "quantity": 10}],
        "open_orders": [],
    }
    assert infer_symbol_broker_state("SPY", snap_ok_pos) == "position_confirmed"


def test_account_metrics_from_summaries_pnl_tags() -> None:
    ns = lambda **kw: type("O", (), kw)()

    summaries = [
        ns(
            account_id="DU1",
            currency="USD",
            net_liquidation=999.25,
            available_funds=100.0,
            buying_power=200.0,
            total_cash=50.0,
            raw={
                "UnrealizedPnL": {"value": "1.25", "currency": "USD"},
                "RealizedPnL": {"value": "-3.40", "currency": "USD"},
            },
        )
    ]

    metrics = _account_metrics_from_summaries(summaries)  # type: ignore[arg-type]
    assert metrics["account_id"] == "DU1"
    assert pytest.approx(metrics["unrealized_pnl"]) == 1.25
    assert pytest.approx(metrics["realized_pnl"]) == -3.40

