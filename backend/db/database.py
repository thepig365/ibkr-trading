"""Async SQLite database access and schema migrations.

The implementation keeps SQLite writes parameterized and runs blocking database
work in worker threads so FastAPI's event loop is not blocked.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from backend.config import resolve_project_path
from backend.db.models import (
    AccountSnapshot,
    CandleSnapshot,
    DailyPerformanceRecord,
    ScaleInRecord,
    SignalRecord,
    TradeRecord,
)

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trades (
    trade_id            TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    direction           TEXT NOT NULL,
    entry_price         REAL NOT NULL,
    entry_time          DATETIME NOT NULL,
    entry_shares        INTEGER NOT NULL,
    entry_reason        TEXT,
    entry_signal_score  REAL,
    entry_fvg_top       REAL,
    entry_fvg_bottom    REAL,
    stop_loss           REAL NOT NULL,
    take_profit         REAL NOT NULL,
    risk_amount         REAL,
    exit_price          REAL,
    exit_time           DATETIME,
    exit_shares         INTEGER,
    exit_reason         TEXT,
    realized_pnl        REAL,
    realized_r          REAL,
    holding_minutes     INTEGER,
    trailing_activated  BOOLEAN DEFAULT FALSE,
    trailing_stop_final REAL,
    max_price_reached   REAL,
    status              TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS scale_ins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    TEXT REFERENCES trades(trade_id),
    price       REAL,
    shares      INTEGER,
    time        DATETIME,
    reason      TEXT,
    time_unix   INTEGER
);

CREATE TABLE IF NOT EXISTS candle_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    TEXT REFERENCES trades(trade_id),
    symbol      TEXT,
    timeframe   TEXT DEFAULT '1m',
    timestamp   DATETIME,
    time_unix   INTEGER,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER
);

CREATE TABLE IF NOT EXISTS daily_performance (
    date              DATE PRIMARY KEY,
    starting_equity   REAL,
    ending_equity     REAL,
    daily_pnl         REAL,
    trades_count      INTEGER,
    wins              INTEGER,
    losses            INTEGER,
    win_rate          REAL,
    avg_r             REAL,
    max_drawdown_pct  REAL,
    capital_used      REAL,
    circuit_broken    BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id    TEXT PRIMARY KEY,
    symbol       TEXT,
    strategy     TEXT,
    direction    TEXT,
    timestamp    DATETIME,
    score        REAL,
    auto_execute BOOLEAN,
    executed     BOOLEAN,
    reject_reason TEXT,
    entry_price  REAL,
    stop_loss    REAL,
    take_profit  REAL,
    reason       TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    timestamp        DATETIME PRIMARY KEY,
    net_liquidation  REAL,
    cash             REAL,
    unrealized_pnl   REAL,
    realized_pnl_day REAL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candle_unique
ON candle_snapshots(symbol, timeframe, timestamp);

CREATE INDEX IF NOT EXISTS idx_candle_symbol_time
ON candle_snapshots(symbol, timeframe, timestamp);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_entry_time
ON trades(symbol, entry_time);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
ON signals(symbol, timestamp);
"""


class Database:
    """Thread-backed async SQLite helper for the trading engine."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = resolve_project_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Create all Paper-phase tables and indexes if they do not exist."""

        await asyncio.to_thread(self._initialize_sync)
        logger.info("SQLite database initialized at %s", self.db_path)

    async def close(self) -> None:
        """Close hook kept for interface symmetry. Connections are per operation."""

        return None

    async def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        """Execute a parameterized write statement and return affected rows."""

        return await asyncio.to_thread(self._execute_sync, query, params)

    async def fetch_one(
        self, query: str, params: Sequence[Any] = ()
    ) -> Optional[dict[str, Any]]:
        """Fetch one row as a dict."""

        return await asyncio.to_thread(self._fetch_one_sync, query, params)

    async def fetch_all(
        self, query: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        """Fetch all rows as dicts."""

        return await asyncio.to_thread(self._fetch_all_sync, query, params)

    async def insert_trade(self, trade: TradeRecord) -> None:
        """Insert a trade row."""

        row = self._record_to_dict(trade)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        await self.execute(
            f"INSERT INTO trades ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    async def update_trade_close(
        self,
        trade_id: str,
        *,
        exit_price: float,
        exit_time: datetime,
        exit_shares: int,
        exit_reason: str,
        realized_pnl: float,
        realized_r: float,
        holding_minutes: int,
        trailing_activated: bool,
        trailing_stop_final: Optional[float],
        max_price_reached: Optional[float],
    ) -> None:
        """Mark an open trade as closed with required chart annotation fields."""

        await self.execute(
            """
            UPDATE trades
            SET exit_price = ?, exit_time = ?, exit_shares = ?, exit_reason = ?,
                realized_pnl = ?, realized_r = ?, holding_minutes = ?,
                trailing_activated = ?, trailing_stop_final = ?,
                max_price_reached = ?, status = 'closed'
            WHERE trade_id = ?
            """,
            (
                exit_price,
                self._serialize_value(exit_time),
                exit_shares,
                exit_reason,
                realized_pnl,
                realized_r,
                holding_minutes,
                int(trailing_activated),
                trailing_stop_final,
                max_price_reached,
                trade_id,
            ),
        )

    async def insert_scale_in(self, scale_in: ScaleInRecord) -> None:
        """Insert a scale-in record."""

        row = self._record_to_dict(scale_in, skip_none_id=True)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        await self.execute(
            f"INSERT INTO scale_ins ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    async def insert_candle_snapshot(self, candle: CandleSnapshot) -> None:
        """Insert a candle snapshot, ignoring duplicate symbol/timeframe/timestamp rows."""

        row = self._record_to_dict(candle, skip_none_id=True)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        await self.execute(
            f"INSERT OR IGNORE INTO candle_snapshots ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    async def upsert_daily_performance(
        self, performance: DailyPerformanceRecord
    ) -> None:
        """Insert or replace a daily performance row."""

        row = self._record_to_dict(performance)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(f"{key}=excluded.{key}" for key in row if key != "date")
        await self.execute(
            f"""
            INSERT INTO daily_performance ({columns}) VALUES ({placeholders})
            ON CONFLICT(date) DO UPDATE SET {updates}
            """,
            tuple(row.values()),
        )

    async def insert_signal(self, signal: SignalRecord) -> None:
        """Insert a signal log row."""

        row = self._record_to_dict(signal)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        await self.execute(
            f"INSERT INTO signals ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    async def insert_account_snapshot(self, snapshot: AccountSnapshot) -> None:
        """Insert or replace an account snapshot."""

        row = self._record_to_dict(snapshot)
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(
            f"{key}=excluded.{key}" for key in row if key != "timestamp"
        )
        await self.execute(
            f"""
            INSERT INTO account_snapshots ({columns}) VALUES ({placeholders})
            ON CONFLICT(timestamp) DO UPDATE SET {updates}
            """,
            tuple(row.values()),
        )

    async def fetch_trade(self, trade_id: str) -> Optional[dict[str, Any]]:
        """Fetch one trade by ID."""

        return await self.fetch_one("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))

    async def fetch_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch recent trades."""

        return await self.fetch_all(
            "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
        )

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Fetch recent candle snapshots for a symbol and timeframe."""

        return await self.fetch_all(
            """
            SELECT * FROM candle_snapshots
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (symbol, timeframe, limit),
        )

    async def fetch_daily_performance(
        self, limit: int = 60
    ) -> list[dict[str, Any]]:
        """Fetch recent daily performance rows."""

        return await self.fetch_all(
            "SELECT * FROM daily_performance ORDER BY date DESC LIMIT ?", (limit,)
        )

    async def fetch_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch recent signal logs."""

        return await self.fetch_all(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

    async def fetch_account_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch recent account snapshots."""

        return await self.fetch_all(
            "SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.commit()

    def _execute_sync(self, query: str, params: Sequence[Any]) -> int:
        serialized_params = tuple(self._serialize_value(item) for item in params)
        with self._connect() as connection:
            cursor = connection.execute(query, serialized_params)
            connection.commit()
            return cursor.rowcount

    def _fetch_one_sync(
        self, query: str, params: Sequence[Any]
    ) -> Optional[dict[str, Any]]:
        serialized_params = tuple(self._serialize_value(item) for item in params)
        with self._connect() as connection:
            cursor = connection.execute(query, serialized_params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def _fetch_all_sync(self, query: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        serialized_params = tuple(self._serialize_value(item) for item in params)
        with self._connect() as connection:
            cursor = connection.execute(query, serialized_params)
            return [dict(row) for row in cursor.fetchall()]

    def _record_to_dict(
        self, record: Any, *, skip_none_id: bool = False
    ) -> dict[str, Any]:
        if not is_dataclass(record):
            raise TypeError("record must be a dataclass instance")

        row = asdict(record)
        if skip_none_id and row.get("id") is None:
            row.pop("id", None)
        return {key: self._serialize_value(value) for key, value in row.items()}

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise ValueError(f"Naive datetime is not allowed: {value!r}")
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, bool):
            return int(value)
        return value
