"""Tests for positions snapshot flat handling."""

from __future__ import annotations

from pathlib import Path

from bot.config import load_config
from bot.ibkr_client import PositionRow
from bot.journal import POSITION_SNAPSHOT_FLAT, Journal


def _pos(sym: str, account: str = "DU1") -> dict:
    return PositionRow(
        account=account,
        symbol=sym,
        sec_type="STK",
        exchange="SMART",
        currency="USD",
        position=10.0,
        avg_cost=1.0,
    ).to_dict()


def test_empty_snapshot_advances_max_ts_and_clears_symbols(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    j = Journal(cfg)
    j.record_positions_snapshot([_pos("AAPL", account="DU1")], account_id="DU1")
    assert j.latest_local_position_symbols() == {"AAPL"}
    j.record_positions_snapshot([], account_id="DU1")
    assert j.latest_local_position_symbols() == set()


def test_flat_row_exists_without_polluting_open_symbols(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    j = Journal(cfg)
    j.record_positions_snapshot([], account_id="X")
    with j.connection() as conn:
        cur = conn.execute(
            "SELECT symbol FROM positions_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row and row[0] == POSITION_SNAPSHOT_FLAT
