"""SQLite migration + parameterized insert tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
import pytz

from backend.db.database import Database
from backend.db.models import CandleSnapshot

NY = pytz.timezone("America/New_York")


@pytest.mark.asyncio
async def test_migration_creates_all_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "trades.db")
    await db.initialize()

    expected = {
        "trades",
        "scale_ins",
        "candle_snapshots",
        "daily_performance",
        "signals",
        "account_snapshots",
    }
    rows = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    actual = {row["name"] for row in rows}
    assert expected.issubset(actual)


@pytest.mark.asyncio
async def test_candle_snapshot_insert_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "trades.db")
    await db.initialize()
    timestamp = NY.localize(datetime(2026, 4, 29, 10, 0))
    candle = CandleSnapshot(
        symbol="SPY",
        timeframe="1m",
        timestamp=timestamp,
        time_unix=int(timestamp.timestamp()),
        open=500.1,
        high=500.5,
        low=499.9,
        close=500.4,
        volume=10000,
    )
    await db.insert_candle_snapshot(candle)
    await db.insert_candle_snapshot(candle)
    rows = await db.fetch_all(
        "SELECT * FROM candle_snapshots WHERE symbol = ?", ("SPY",)
    )
    assert len(rows) == 1
