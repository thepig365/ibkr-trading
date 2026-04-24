"""Persistence layer: SQLite + JSONL audit logs.

The SQLite database is the structured store. The JSONL files are
append-only audit trails that are easy to inspect from the shell and
hard to silently mutate.

No order placement happens here - this module only records facts.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import AppConfig


SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS account_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        account_id TEXT NOT NULL,
        net_liquidation REAL,
        total_cash REAL,
        buying_power REAL,
        available_funds REAL,
        currency TEXT,
        raw_json TEXT
    )
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        perm_id INTEGER,
        order_id INTEGER,
        account_id TEXT,
        symbol TEXT,
        sec_type TEXT,
        action TEXT,
        order_type TEXT,
        total_quantity REAL,
        lmt_price REAL,
        aux_price REAL,
        tif TEXT,
        status TEXT,
        source TEXT,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        exec_id TEXT UNIQUE,
        perm_id INTEGER,
        order_id INTEGER,
        account_id TEXT,
        symbol TEXT,
        sec_type TEXT,
        side TEXT,
        shares REAL,
        price REAL,
        exchange TEXT,
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bot_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        level TEXT NOT NULL,
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        payload_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        chat_id TEXT,
        text TEXT NOT NULL,
        delivered INTEGER NOT NULL,
        error TEXT
    )
    """,
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class Journal:
    """SQLite + JSONL writer.

    Safe to instantiate even when the data directory does not yet
    exist - it will be created on first use.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.sqlite_path = cfg.absolute(cfg.settings.paths.sqlite_file)
        self.orders_jsonl = cfg.absolute(cfg.settings.paths.orders_jsonl)
        self.executions_jsonl = cfg.absolute(cfg.settings.paths.executions_jsonl)
        self.account_snapshots_jsonl = cfg.absolute(
            cfg.settings.paths.account_snapshots_jsonl
        )
        for p in (
            self.sqlite_path,
            self.orders_jsonl,
            self.executions_jsonl,
            self.account_snapshots_jsonl,
        ):
            _ensure_parent(p)
        self.init_db()

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------
    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.sqlite_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connection() as conn:
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(stmt)

    # ------------------------------------------------------------------
    # JSONL append helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        _ensure_parent(path)
        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # Public write methods
    # ------------------------------------------------------------------
    def record_account_snapshot(self, summary: dict[str, Any]) -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots
                  (ts_utc, account_id, net_liquidation, total_cash,
                   buying_power, available_funds, currency, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    summary.get("account_id", ""),
                    summary.get("net_liquidation"),
                    summary.get("total_cash"),
                    summary.get("buying_power"),
                    summary.get("available_funds"),
                    summary.get("currency"),
                    json.dumps(summary.get("raw", {})),
                ),
            )
        self._append_jsonl(self.account_snapshots_jsonl, {"ts_utc": ts, **summary})

    def record_positions_snapshot(self, positions: list[dict[str, Any]]) -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            for p in positions:
                conn.execute(
                    """
                    INSERT INTO positions_snapshots
                      (ts_utc, account_id, symbol, sec_type, exchange,
                       currency, position, avg_cost)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        p.get("account", ""),
                        p.get("symbol", ""),
                        p.get("sec_type"),
                        p.get("exchange"),
                        p.get("currency"),
                        p.get("position", 0),
                        p.get("avg_cost"),
                    ),
                )

    def record_open_order(self, order: dict[str, Any], source: str = "broker") -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO orders
                  (ts_utc, perm_id, order_id, account_id, symbol, sec_type,
                   action, order_type, total_quantity, lmt_price, aux_price,
                   tif, status, source, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    order.get("perm_id"),
                    order.get("order_id"),
                    order.get("account"),
                    order.get("symbol"),
                    order.get("sec_type"),
                    order.get("action"),
                    order.get("order_type"),
                    order.get("total_quantity"),
                    order.get("lmt_price"),
                    order.get("aux_price"),
                    order.get("tif"),
                    order.get("status"),
                    source,
                    json.dumps(order),
                ),
            )
        self._append_jsonl(
            self.orders_jsonl, {"ts_utc": ts, "source": source, **order}
        )

    def record_execution(self, execution: dict[str, Any]) -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO executions
                  (ts_utc, exec_id, perm_id, order_id, account_id, symbol,
                   sec_type, side, shares, price, exchange, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    execution.get("exec_id"),
                    execution.get("perm_id"),
                    execution.get("order_id"),
                    execution.get("account"),
                    execution.get("symbol"),
                    execution.get("sec_type"),
                    execution.get("side"),
                    execution.get("shares"),
                    execution.get("price"),
                    execution.get("exchange"),
                    json.dumps(execution),
                ),
            )
        self._append_jsonl(self.executions_jsonl, {"ts_utc": ts, **execution})

    def record_event(
        self,
        category: str,
        message: str,
        level: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO bot_events (ts_utc, level, category, message, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, level.upper(), category, message, json.dumps(payload or {})),
            )

    def record_telegram_message(
        self, chat_id: str | None, text: str, delivered: bool, error: str | None
    ) -> None:
        ts = _utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO telegram_messages (ts_utc, chat_id, text, delivered, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, chat_id, text, 1 if delivered else 0, error),
            )

    # ------------------------------------------------------------------
    # Read helpers (used by reconciliation)
    # ------------------------------------------------------------------
    def latest_local_open_order_perm_ids(self) -> set[int]:
        """Return perm_ids of orders we last saw as open in our journal.

        Used by reconciliation to detect orders that exist at the broker
        but are unknown to the bot. The result is a best-effort view: we
        look at the most recent status per perm_id.
        """
        with self.connection() as conn:
            cur = conn.execute(
                """
                SELECT perm_id, status FROM orders
                WHERE perm_id IS NOT NULL
                  AND id IN (
                      SELECT MAX(id) FROM orders
                      WHERE perm_id IS NOT NULL
                      GROUP BY perm_id
                  )
                """
            )
            return {
                row[0]
                for row in cur.fetchall()
                if row[1] is None or str(row[1]).lower() not in {"cancelled", "filled", "inactive"}
            }

    def latest_local_position_symbols(self) -> set[str]:
        """Return the set of symbols in our most recent positions snapshot."""
        with self.connection() as conn:
            cur = conn.execute("SELECT MAX(ts_utc) FROM positions_snapshots")
            row = cur.fetchone()
            if not row or not row[0]:
                return set()
            ts = row[0]
            cur = conn.execute(
                "SELECT symbol FROM positions_snapshots WHERE ts_utc = ?", (ts,)
            )
            return {r[0] for r in cur.fetchall() if r[0]}
