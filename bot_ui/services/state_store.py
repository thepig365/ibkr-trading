"""StateStore abstraction for the local Strategy Lab UI.

Two concrete implementations:

* :class:`LocalFileStateStore` — reads JSON / JSONL / CSV files written
  by the existing ``bot/`` package (works today, on the local laptop).
* :class:`DatabaseStateStore` — placeholder for the future Vercel /
  Supabase deployment. Methods raise :class:`NotImplementedError` so a
  partial port can never silently look "healthy".

Important: this module must NOT import :mod:`bot.broker`,
:mod:`bot.ibkr_client`, or any TWS-touching code. It must work even
when TWS is not running and when most data files are missing.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class AccountSummary:
    account_id: str = ""
    net_liquidation: float | None = None
    total_cash: float | None = None
    buying_power: float | None = None
    available_funds: float | None = None
    currency: str = ""
    snapshot_ts_utc: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.account_id and self.net_liquidation is None


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    position: float
    avg_cost: float | None = None
    account_id: str = ""


@dataclass(frozen=True)
class WatchlistEntry:
    symbol: str
    reason: list[str] = field(default_factory=list)
    latest_price: float | None = None
    relative_volume: float | None = None
    blocked: bool = False


@dataclass(frozen=True)
class WatchlistView:
    date: str = ""
    source: str = ""
    symbols: list[WatchlistEntry] = field(default_factory=list)
    file_path: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.symbols


@dataclass(frozen=True)
class SignalRow:
    symbol: str
    alignment_category: str = ""
    mtf_alignment_score: float | None = None
    eligible_for_future_paper_trade: bool = False


@dataclass(frozen=True)
class SignalsView:
    date: str = ""
    source: str = ""
    symbols_scanned: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    top: list[SignalRow] = field(default_factory=list)
    eligible: list[str] = field(default_factory=list)
    file_path: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.symbols_scanned == 0 and not self.top


@dataclass(frozen=True)
class LoopStatus:
    last_cycle_utc: str = ""
    last_status: str = ""
    last_reason: str = ""
    last_full_alignment_count: int = 0
    last_orders_submitted: int = 0
    cycles: int = 0
    kill_switch: bool = False
    runtime_mtf_on: bool = False
    runtime_mtf_off_explicit: bool = False
    last_heartbeat_ts: float | None = None
    file_path: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.last_cycle_utc and self.cycles == 0


@dataclass(frozen=True)
class RuntimeFlags:
    """Runtime toggles read from on-disk files.

    The kill switch lives at ``data/KILL_SWITCH`` (the canonical path used by
    ``bot/auto_paper_mtf.is_kill_switch_active`` and Telegram /kill /resume).
    The MTF auto-paper toggle lives at ``data/runtime/mtf_auto_paper_enabled``
    (the canonical path used by ``bot/auto_paper_mtf`` and the auto-paper loop).
    """

    kill_switch_active: bool = False
    mtf_auto_paper_enabled: bool = False
    mtf_auto_paper_explicit_off: bool = False
    runtime_dir: str | None = None
    kill_switch_path: str | None = None
    mtf_auto_paper_enabled_path: str | None = None


@dataclass(frozen=True)
class SafetyView:
    """High-level "is this safe?" view shown on the dashboard."""

    paper_only: bool = True
    block_live_trading: bool = True
    account_mode: str = "paper"
    ibkr_account_mode_env: str = "paper"
    issues: list[str] = field(default_factory=list)


class StateStore(Protocol):
    """Read-only view of operational state used by the UI."""

    def account_summary(self) -> AccountSummary: ...
    def positions(self) -> list[PositionRow]: ...
    def watchlist(self) -> WatchlistView: ...
    def signals(self) -> SignalsView: ...
    def loop_status(self) -> LoopStatus: ...
    def runtime_flags(self) -> RuntimeFlags: ...
    def safety_view(self) -> SafetyView: ...
    def list_log_files(self) -> list[Path]: ...
    def tail_file(self, path: Path, max_bytes: int = 64_000) -> str: ...


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


# Canonical project-relative paths shared with the worker (bot/auto_paper_*.py
# and bot/telegram_commands.py). These MUST stay in sync with the existing
# auto-paper loop and Telegram /kill /resume / /auto_mtf_on / /auto_mtf_off
# handlers, otherwise the UI and worker would disagree on safety state.
#
#   - Kill switch: ``data/KILL_SWITCH`` (file-presence check, written by
#     /kill, removed by /resume; see bot/auto_paper_mtf.is_kill_switch_active).
#   - MTF auto-paper enabled: ``data/runtime/mtf_auto_paper_enabled`` whose
#     contents are "1"/"on"/"true" => ON, "0"/"off"/"false" => explicit OFF
#     (see bot/auto_paper_mtf.is_runtime_mtf_auto_enabled /
#     is_runtime_mtf_auto_disabled_explicit).
#   - Loop state snapshot: ``data/runtime/auto_paper_loop_state.json``
#     written by bot/auto_paper_loop.run_auto_paper_mtf_loop.
KILL_SWITCH_RELPATH = "data/KILL_SWITCH"
MTF_AUTO_PAPER_ENABLED_RELPATH = "data/runtime/mtf_auto_paper_enabled"
LOOP_STATE_RELPATH = "data/runtime/auto_paper_loop_state.json"

# Backwards-compatible filename constants (used by older imports/tests).
KILL_SWITCH_FILE = "KILL_SWITCH"
MTF_AUTO_PAPER_ENABLED_FILE = "mtf_auto_paper_enabled"
LOOP_STATE_FILE = "auto_paper_loop_state.json"


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _safe_read_text(path: Path, *, max_bytes: int = 1_000_000) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            blob = f.read()
        return blob.decode("utf-8", errors="replace")
    except OSError:
        return None


def _read_jsonl_last_line(path: Path) -> dict[str, Any] | None:
    """Return the parsed last JSON object from a JSONL file, or None."""
    if not path.exists() or not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            try:
                f.seek(-4096, os.SEEK_END)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    last = ""
    for line in tail.splitlines():
        line = line.strip()
        if line:
            last = line
    if not last:
        return None
    try:
        obj = json.loads(last)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _list_glob(root: Path, pattern: str) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.glob(pattern))


class LocalFileStateStore:
    """Read state from the project's local files. NEVER touches IBKR.

    Designed to be silent-safe: every accessor returns a typed empty
    object instead of raising when the underlying file is absent or
    malformed. The UI is therefore never blocked by missing data.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data"
        self.runtime_dir = self.data_dir / "runtime"
        self.watchlists_dir = self.data_dir / "watchlists"
        self.mtf_smc_dir = self.data_dir / "mtf_smc"
        self.auto_paper_loop_dir = self.data_dir / "auto_paper_loop"
        self.account_snapshots_jsonl = self.data_dir / "account_snapshots.jsonl"
        self.sqlite_path = self.data_dir / "trading_bot.sqlite"
        self.logs_dir = self.project_root / "logs"
        # Canonical paths shared with the worker (bot/auto_paper_*.py).
        # These MUST equal the paths the worker writes / reads.
        self.kill_switch_path = self.project_root / KILL_SWITCH_RELPATH
        self.mtf_auto_paper_enabled_path = self.project_root / MTF_AUTO_PAPER_ENABLED_RELPATH
        self.loop_state_path = self.project_root / LOOP_STATE_RELPATH

    # ------------------------------------------------------------------
    # Account / positions
    # ------------------------------------------------------------------
    def account_summary(self) -> AccountSummary:
        # Walk the JSONL backwards and pick the most recent row that has
        # an account_id matching DUx... (paper) or any non-empty/non-"All".
        # If everything fails, return AccountSummary().
        path = self.account_snapshots_jsonl
        if not path.exists():
            return AccountSummary()
        try:
            with path.open("rb") as f:
                try:
                    f.seek(-32_000, os.SEEK_END)
                except OSError:
                    f.seek(0)
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return AccountSummary()
        best: dict[str, Any] | None = None
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            acct = str(obj.get("account_id") or "")
            if acct and acct.lower() != "all":
                best = obj
                break
            if best is None:
                best = obj
        if best is None:
            return AccountSummary()
        return AccountSummary(
            account_id=str(best.get("account_id") or ""),
            net_liquidation=_to_float(best.get("net_liquidation")),
            total_cash=_to_float(best.get("total_cash")),
            buying_power=_to_float(best.get("buying_power")),
            available_funds=_to_float(best.get("available_funds")),
            currency=str(best.get("currency") or ""),
            snapshot_ts_utc=str(best.get("ts_utc") or ""),
        )

    def positions(self) -> list[PositionRow]:
        """Read latest non-zero positions from sqlite.

        Returns an empty list (not an error) when sqlite is missing.
        """
        if not self.sqlite_path.exists():
            return []
        try:
            with sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT MAX(ts_utc) AS ts FROM positions_snapshots"
                ).fetchone()
                if not row or not row["ts"]:
                    return []
                latest_ts = row["ts"]
                rows = conn.execute(
                    """
                    SELECT account_id, symbol, position, avg_cost
                    FROM positions_snapshots
                    WHERE ts_utc = ?
                    ORDER BY symbol
                    """,
                    (latest_ts,),
                ).fetchall()
        except sqlite3.Error:
            return []

        out: list[PositionRow] = []
        for r in rows:
            sym = str(r["symbol"] or "")
            if not sym or sym.startswith("__ACCOUNT_NO_POSITIONS__"):
                continue
            try:
                qty = float(r["position"] or 0.0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty == 0.0:
                continue
            try:
                cost = float(r["avg_cost"]) if r["avg_cost"] is not None else None
            except (TypeError, ValueError):
                cost = None
            out.append(
                PositionRow(
                    symbol=sym,
                    position=qty,
                    avg_cost=cost,
                    account_id=str(r["account_id"] or ""),
                )
            )
        return out

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------
    def watchlist(self) -> WatchlistView:
        path = self._latest_watchlist_path()
        if path is None:
            return WatchlistView()
        data = _safe_read_json(path)
        if not data:
            return WatchlistView(file_path=str(path))
        items_raw = data.get("symbols") or []
        symbols: list[WatchlistEntry] = []
        for item in items_raw:
            if isinstance(item, str):
                symbols.append(WatchlistEntry(symbol=item))
                continue
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").upper().strip()
            if not sym:
                continue
            reason_raw = item.get("reason") or []
            if isinstance(reason_raw, str):
                reasons = [reason_raw]
            elif isinstance(reason_raw, list):
                reasons = [str(r) for r in reason_raw]
            else:
                reasons = []
            symbols.append(
                WatchlistEntry(
                    symbol=sym,
                    reason=reasons,
                    latest_price=_to_float(item.get("latest_price")),
                    relative_volume=_to_float(item.get("relative_volume")),
                    blocked=bool(item.get("blocked")),
                )
            )
        return WatchlistView(
            date=str(data.get("date") or ""),
            source=str(data.get("source") or ""),
            symbols=symbols,
            file_path=str(path),
        )

    def _latest_watchlist_path(self) -> Path | None:
        if not self.watchlists_dir.exists():
            return None
        candidates = sorted(self.watchlists_dir.glob("*-dynamic-watchlist.json"))
        if not candidates:
            return None
        return candidates[-1]

    # ------------------------------------------------------------------
    # Signals (MTF SMC)
    # ------------------------------------------------------------------
    def signals(self) -> SignalsView:
        path = self._latest_signals_path()
        if path is None:
            return SignalsView()
        data = _safe_read_json(path)
        if not data:
            return SignalsView(file_path=str(path))
        top_raw = data.get("top_by_alignment_score") or data.get("items") or []
        top: list[SignalRow] = []
        for item in top_raw:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").upper().strip()
            if not sym:
                continue
            top.append(
                SignalRow(
                    symbol=sym,
                    alignment_category=str(item.get("alignment_category") or ""),
                    mtf_alignment_score=_to_float(item.get("mtf_alignment_score")),
                    eligible_for_future_paper_trade=bool(
                        item.get("eligible_for_future_paper_trade")
                    ),
                )
            )
        eligible_raw = data.get("eligible_for_future_paper_trade") or []
        eligible: list[str] = []
        if isinstance(eligible_raw, list):
            for el in eligible_raw:
                if isinstance(el, str):
                    eligible.append(el)
                elif isinstance(el, dict) and el.get("symbol"):
                    eligible.append(str(el["symbol"]))
        counts_raw = data.get("counts") or {}
        counts: dict[str, int] = {}
        if isinstance(counts_raw, dict):
            for k, v in counts_raw.items():
                try:
                    counts[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue
        return SignalsView(
            date=str(data.get("date") or ""),
            source=str(data.get("source") or ""),
            symbols_scanned=int(data.get("symbols_scanned") or 0),
            counts=counts,
            top=top,
            eligible=eligible,
            file_path=str(path),
        )

    def _latest_signals_path(self) -> Path | None:
        if not self.mtf_smc_dir.exists():
            return None
        candidates = sorted(self.mtf_smc_dir.glob("*-watchlist-mtf-smc-summary.json"))
        if not candidates:
            return None
        return candidates[-1]

    # ------------------------------------------------------------------
    # Loop status / runtime flags
    # ------------------------------------------------------------------
    def loop_status(self) -> LoopStatus:
        # Prefer auto_paper_loop_state.json (single-shot snapshot); fall
        # back to the latest line of *-loop.jsonl if the snapshot file
        # is missing.
        state_path = self.loop_state_path
        data = _safe_read_json(state_path)
        if data is None:
            data = self._latest_loop_jsonl_line()
            file_path = self._latest_loop_jsonl_path()
        else:
            file_path = state_path
        if data is None:
            return LoopStatus()
        return LoopStatus(
            last_cycle_utc=str(data.get("last_cycle_utc") or data.get("timestamp") or ""),
            last_status=str(data.get("last_status") or data.get("status") or ""),
            last_reason=str(data.get("last_reason") or data.get("reason") or ""),
            last_full_alignment_count=int(
                data.get("last_full_alignment_count")
                or data.get("full_alignment_count")
                or 0
            ),
            last_orders_submitted=int(
                data.get("last_orders_submitted") or data.get("orders_submitted") or 0
            ),
            cycles=int(data.get("cycles") or data.get("cycle") or 0),
            kill_switch=bool(data.get("kill_switch")),
            runtime_mtf_on=bool(data.get("runtime_mtf_on")),
            runtime_mtf_off_explicit=bool(data.get("runtime_mtf_off_explicit")),
            last_heartbeat_ts=_to_float(data.get("last_heartbeat_ts")),
            file_path=str(file_path) if file_path else None,
        )

    def _latest_loop_jsonl_path(self) -> Path | None:
        if not self.auto_paper_loop_dir.exists():
            return None
        candidates = sorted(self.auto_paper_loop_dir.glob("*-loop.jsonl"))
        if not candidates:
            return None
        return candidates[-1]

    def _latest_loop_jsonl_line(self) -> dict[str, Any] | None:
        path = self._latest_loop_jsonl_path()
        if path is None:
            return None
        return _read_jsonl_last_line(path)

    def runtime_flags(self) -> RuntimeFlags:
        # Use the canonical paths shared with the worker so the UI shows the
        # exact same kill-switch / auto-flag state as bot/auto_paper_*.py
        # and bot/telegram_commands.py.
        ks = self.kill_switch_path
        en = self.mtf_auto_paper_enabled_path
        kill_switch = ks.exists() and ks.is_file()
        # mtf_auto_paper_enabled: file presence + content "1"/"true" => on
        # explicit "0"/"off" => explicit off
        explicit_off = False
        enabled = False
        if en.exists() and en.is_file():
            try:
                content = en.read_text(encoding="utf-8").strip().lower()
            except OSError:
                content = ""
            if content in {"0", "off", "false", "no"}:
                explicit_off = True
            elif content in {"1", "on", "true", "yes", ""}:
                enabled = True
        return RuntimeFlags(
            kill_switch_active=kill_switch,
            mtf_auto_paper_enabled=enabled,
            mtf_auto_paper_explicit_off=explicit_off,
            runtime_dir=str(self.runtime_dir),
            kill_switch_path=str(ks),
            mtf_auto_paper_enabled_path=str(en),
        )

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------
    def safety_view(self) -> SafetyView:
        ibkr_mode = (os.environ.get("IBKR_ACCOUNT_MODE") or "paper").strip().lower()
        issues: list[str] = []
        if ibkr_mode != "paper":
            issues.append(
                f"IBKR_ACCOUNT_MODE is {ibkr_mode!r}; this UI is paper-only."
            )
        return SafetyView(
            paper_only=True,
            block_live_trading=True,
            account_mode="paper",
            ibkr_account_mode_env=ibkr_mode,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------
    def list_log_files(self) -> list[Path]:
        roots: Iterable[Path] = (self.logs_dir, self.auto_paper_loop_dir, self.data_dir)
        out: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.name.startswith("."):
                    continue
                if not (p.suffix in {".log", ".jsonl"} or p.name.endswith(".log")):
                    continue
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                out.append(p)
        # newest first
        out.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return out

    def tail_file(self, path: Path, max_bytes: int = 64_000) -> str:
        # Refuse to read outside project_root (defence in depth).
        rp = Path(path).resolve()
        try:
            rp.relative_to(self.project_root)
        except ValueError:
            return ""
        text = _safe_read_text(rp, max_bytes=max_bytes)
        return text or ""


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Database backend (placeholder for future Vercel + Supabase deployment)
# ---------------------------------------------------------------------------


class DatabaseStateStore:
    """Placeholder for the future Postgres/Supabase-backed state store.

    Methods raise :class:`NotImplementedError`. This class exists so the
    UI can import the type and so future cloud work has a clear seam.
    Activate by setting ``STRATEGY_LAB_BACKEND=remote`` once
    implemented.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")

    def _not_yet(self) -> "NotImplementedError":
        return NotImplementedError(
            "DatabaseStateStore is a placeholder. Set STRATEGY_LAB_BACKEND=local "
            "for the supported local backend, or implement this class once the "
            "Vercel + Supabase wiring exists."
        )

    def account_summary(self) -> AccountSummary:  # pragma: no cover - stub
        raise self._not_yet()

    def positions(self) -> list[PositionRow]:  # pragma: no cover - stub
        raise self._not_yet()

    def watchlist(self) -> WatchlistView:  # pragma: no cover - stub
        raise self._not_yet()

    def signals(self) -> SignalsView:  # pragma: no cover - stub
        raise self._not_yet()

    def loop_status(self) -> LoopStatus:  # pragma: no cover - stub
        raise self._not_yet()

    def runtime_flags(self) -> RuntimeFlags:  # pragma: no cover - stub
        raise self._not_yet()

    def safety_view(self) -> SafetyView:  # pragma: no cover - stub
        raise self._not_yet()

    def list_log_files(self) -> list[Path]:  # pragma: no cover - stub
        raise self._not_yet()

    def tail_file(self, path: Path, max_bytes: int = 64_000) -> str:  # pragma: no cover - stub
        raise self._not_yet()


def get_state_store(project_root: Path) -> StateStore:
    """Factory for the currently-configured state backend."""
    backend = (os.environ.get("STRATEGY_LAB_BACKEND") or "local").strip().lower()
    if backend == "local":
        return LocalFileStateStore(project_root)
    if backend == "remote":
        return DatabaseStateStore()
    raise ValueError(
        f"Unknown STRATEGY_LAB_BACKEND={backend!r}. Use 'local' or 'remote'."
    )


__all__ = [
    "AccountSummary",
    "PositionRow",
    "WatchlistEntry",
    "WatchlistView",
    "SignalRow",
    "SignalsView",
    "LoopStatus",
    "RuntimeFlags",
    "SafetyView",
    "StateStore",
    "LocalFileStateStore",
    "DatabaseStateStore",
    "get_state_store",
    "KILL_SWITCH_FILE",
    "MTF_AUTO_PAPER_ENABLED_FILE",
    "LOOP_STATE_FILE",
]
