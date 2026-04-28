"""Read-only TWS broker snapshot → ``data/runtime/broker_snapshot_last.json``.

UI routes call :func:`load_broker_snapshot` only (local JSON).

:func:`refresh_broker_snapshot` performs an explicit read-only connect via the
``broker_readonly`` client-id roster.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import AppConfig
    from .journal import Journal


BROKER_SNAPSHOT_LAST_RELPATH = "data/runtime/broker_snapshot_last.json"

_SOURCE = "tws_readonly"
_RECENT_EXEC_CAP = 80


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def broker_snapshot_last_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / BROKER_SNAPSHOT_LAST_RELPATH


@dataclass
class BrokerSnapshot:
    checked_at_utc: str = ""
    status: str = "unavailable"
    tws_listening: bool | None = None
    ibkr_connected: bool | None = None
    account_mode: str | None = None
    paper_account: bool | None = None
    positions_count: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders_count: int = 0
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    executions_count: int = 0
    recent_executions: list[dict[str, Any]] = field(default_factory=list)
    error_summary: str | None = None
    source: str = _SOURCE
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_broker_snapshot(project_root: Path | str) -> dict[str, Any] | None:
    """Read cached snapshot JSON — no network."""

    p = broker_snapshot_last_path(project_root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _position_row_to_payload(p: Any) -> dict[str, Any]:
    d = p.to_dict() if hasattr(p, "to_dict") else asdict(p)
    qty = float(d.get("position") or 0)
    avg = float(d.get("avg_cost") or 0)
    mv = abs(qty) * abs(avg) if qty or avg else None
    return {
        "symbol": str(d.get("symbol") or "").upper(),
        "quantity": qty,
        "avg_cost": avg,
        "market_value": mv,
        "account": str(d.get("account") or ""),
        "sec_type": str(d.get("sec_type") or ""),
        "exchange": str(d.get("exchange") or ""),
        "currency": str(d.get("currency") or ""),
    }


def _write_snapshot(root: Path, payload: dict[str, Any]) -> None:
    path = broker_snapshot_last_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def refresh_broker_snapshot(
    *,
    cfg: "AppConfig | None" = None,
    journal: "Journal | None" = None,
) -> BrokerSnapshot:
    """Read-only roster connect; aggregates positions/orders/executions; writes JSON."""

    # Lazy imports keep ``import bot.broker_snapshot`` free of IBKR/async deps (UI safety tests).
    from .broker import Broker
    from .config import load_config
    from .ibkr_connection import connect_readonly_roster_retry

    cfg = cfg or load_config()
    root = Path(cfg.project_root).resolve()
    outcome = connect_readonly_roster_retry(cfg, "broker_readonly")
    checked = _utc_now_iso()
    meta: dict[str, Any] = {
        "client_id_used": outcome.client_id_used,
        "attempted_client_ids": list(outcome.attempted_client_ids or []),
    }

    if outcome.live_blocked is not None:
        snap = BrokerSnapshot(
            checked_at_utc=checked,
            status="error",
            error_summary=str(outcome.live_blocked)[:4000],
            tws_listening=False,
            ibkr_connected=False,
            account_mode=getattr(cfg.ibkr, "account_mode", None),
            paper_account=(str(getattr(cfg.ibkr, "account_mode", "")).lower() == "paper"),
            meta=meta,
        )
        _write_snapshot(root, snap.to_dict())
        return snap

    if outcome.client is None:
        body = outcome.fatal_message or "broker connection unavailable"
        snap = BrokerSnapshot(
            checked_at_utc=checked,
            status="unavailable",
            error_summary=str(body)[:4000],
            account_mode=getattr(cfg.ibkr, "account_mode", None),
            paper_account=(str(getattr(cfg.ibkr, "account_mode", "")).lower() == "paper"),
            meta=meta,
        )
        _write_snapshot(root, snap.to_dict())
        return snap

    client = outcome.client
    broker = Broker(cfg, client, journal)
    snap = BrokerSnapshot(
        checked_at_utc=checked,
        status="ok",
        ibkr_connected=True,
        account_mode=getattr(cfg.ibkr, "account_mode", None),
        paper_account=(str(getattr(cfg.ibkr, "account_mode", "")).lower() == "paper"),
        source=_SOURCE,
        meta=dict(meta),
    )

    try:
        sess = client.session_status_snapshot()
        snap.meta["session"] = sess
        snap.ibkr_connected = bool(sess.get("connected"))
        am = sess.get("account_mode") or getattr(cfg.ibkr, "account_mode", None)
        snap.account_mode = str(am) if am else snap.account_mode
    except Exception as exc:  # noqa: BLE001
        snap.meta["session_snapshot_error"] = repr(exc)

    client_obj = outcome.client

    try:
        pos_rows = broker.get_positions()
        snap.positions = [_position_row_to_payload(p) for p in pos_rows]
        snap.positions_count = len(
            [p for p in snap.positions if abs(float(p.get("quantity") or 0)) > 1e-12]
        )
    except Exception as exc:  # noqa: BLE001
        snap.meta["positions_error"] = repr(exc)

    try:
        ord_rows = broker.get_open_orders()
        snap.open_orders = [
            r.to_dict() if hasattr(r, "to_dict") else asdict(r) for r in ord_rows
        ]
        snap.open_orders_count = len(snap.open_orders)
    except Exception as exc:  # noqa: BLE001
        snap.meta["open_orders_error"] = repr(exc)

    try:
        ex_rows = broker.get_executions()
        snap.executions_count = len(ex_rows)
        recent = sorted(
            (x.to_dict() if hasattr(x, "to_dict") else asdict(x) for x in ex_rows),
            key=lambda d: str(d.get("time") or ""),
            reverse=True,
        )[:_RECENT_EXEC_CAP]
        snap.recent_executions = list(recent)
    except Exception as exc:  # noqa: BLE001
        snap.meta["executions_error"] = repr(exc)
    finally:
        try:
            client_obj.disconnect()
        except Exception:
            pass

    _write_snapshot(root, snap.to_dict())
    return snap


def refresh_broker_snapshot_best_effort(*, cfg: "AppConfig | None" = None) -> BrokerSnapshot:
    """Like :func:`refresh_broker_snapshot` but never raises — writes error envelopes."""

    try:
        return refresh_broker_snapshot(cfg=cfg)
    except Exception as exc:  # noqa: BLE001
        from .config import load_config

        cfg = cfg or load_config()
        root = Path(cfg.project_root).resolve()
        snap = BrokerSnapshot(
            checked_at_utc=_utc_now_iso(),
            status="error",
            error_summary=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}",
        )
        try:
            _write_snapshot(root, snap.to_dict())
        except Exception:
            pass
        return snap


def infer_symbol_broker_state(symbol: str, snap: dict[str, Any] | None) -> str:
    """Coarse broker-state token per trade symbol."""

    sym = (symbol or "").strip().upper()
    if not sym:
        return "unknown"
    if not snap:
        return "not_checked"
    st = str(snap.get("status") or "").lower()
    if st != "ok":
        if st == "unavailable":
            return "broker_unavailable"
        return "broker_error"

    qty = 0.0
    for row in snap.get("positions") or []:
        if str(row.get("symbol") or "").upper() != sym:
            continue
        try:
            qty += float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            pass

    oo = [
        x
        for x in (snap.get("open_orders") or [])
        if str(x.get("symbol") or "").upper() == sym
    ]
    if abs(qty) > 1e-9:
        return "position_confirmed"
    if oo:
        return "has_open_orders"
    return "flat_no_position"


def infer_local_trade_state_token(rec: Any) -> str:
    """Stable token for trades table Local State column."""

    slug = getattr(rec, "status_slug", "") or ""
    sj = getattr(rec, "submitted_to_broker", False)
    raw = getattr(rec, "raw_json", {}) or {}
    submitted = bool(raw.get("submitted"))
    if slug == "skipped":
        return "skipped_decision"
    if slug == "closed":
        return "closed_trade"
    if slug == "protection_incomplete":
        return "protection_incomplete"
    if slug == "rejected":
        return "rejected"
    if slug == "pending":
        return "pending_local"
    if slug == "open":
        if sj:
            return "sent_to_broker_local"
        if submitted:
            return "submitted_local_open"
        return "open_unknown"
    return "unknown_local"



__all__ = [
    "BROKER_SNAPSHOT_LAST_RELPATH",
    "BrokerSnapshot",
    "broker_snapshot_last_path",
    "infer_symbol_broker_state",
    "infer_local_trade_state_token",
    "load_broker_snapshot",
    "refresh_broker_snapshot",
    "refresh_broker_snapshot_best_effort",
]
