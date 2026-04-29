"""Read-only IBKR fills reconciliation vs local Strategy Lab paper order rows.

Loads TWS executions via the existing read-only roster (``broker_readonly``);
never places, cancels, or modifies orders. Persists summaries under ``data/runtime/``
and per-day archives — these paths must not be committed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .config import AppConfig

from .broker import Broker
from .ibkr_client import ExecutionRow
from .ibkr_connection import connect_readonly_roster_retry
from .trade_journal_chart import iso_timestamp_to_utc
from .trade_ledger import TradeLedgerRecord

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

RUNTIME_LAST = "data/runtime/fills_reconciliation_last.json"
RECON_DAY = "data/reconciled_trades/{date}-reconciled-trades.json"
EXEC_DAY = "data/executions/{date}-ibkr-executions.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fills_reconciliation_last_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / RUNTIME_LAST


def _ny_today_iso() -> str:
    return datetime.now(_NY).date().isoformat()


def _parse_day(s: str | None) -> date | None:
    if not s or len(str(s).strip()) < 10:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def execution_row_to_reconciled_fill(r: ExecutionRow) -> dict[str, Any]:
    d = r.to_dict() if hasattr(r, "to_dict") else asdict(r)
    return {
        "exec_id": getattr(r, "exec_id", "") or "",
        "order_id": getattr(r, "order_id", None),
        "perm_id": getattr(r, "perm_id", None),
        "symbol": (getattr(r, "symbol", "") or "").upper(),
        "side": getattr(r, "side", "") or "",
        "quantity": float(getattr(r, "shares", 0) or 0),
        "price": float(getattr(r, "price", 0) or 0),
        "time": getattr(r, "time", None),
        "account": getattr(r, "account", "") or "",
        "raw": d,
    }


def fetch_ibkr_executions_for_reconciliation(
    *,
    cfg: "AppConfig | None" = None,
    date_only: date | None = None,
    since: str | None = None,
    symbols: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read-only roster connect → ``broker.get_executions()`` → normalized dicts."""

    from .config import load_config

    cfg = cfg or load_config()
    outcome = connect_readonly_roster_retry(cfg, "broker_readonly")

    if outcome.live_blocked:
        return [], str(outcome.live_blocked)[:800]
    if outcome.client is None:
        return [], (outcome.fatal_message or "TWS unreachable")[:800]

    broker = Broker(cfg, outcome.client, None)
    try:
        rows = broker.get_executions()
    except Exception as exc:  # noqa: BLE001
        try:
            outcome.client.disconnect()
        except Exception:
            pass
        return [], repr(exc)
    finally:
        try:
            outcome.client.disconnect()
        except Exception:
            pass

    fills: list[dict[str, Any]] = []
    for raw in rows:
        ef = execution_row_to_reconciled_fill(raw)
        tstr = ef.get("time") or ""
        symu = ef.get("symbol") or ""
        if symbols and symu.upper() not in symbols:
            continue
        if since and tstr and str(tstr) < since:
            continue
        if date_only is not None and tstr:
            dt = _exec_time_to_dt(tstr)
            if dt is None:
                continue
            d_ny = dt.astimezone(_NY).date()
            if d_ny != date_only:
                continue
        fills.append(ef)

    fills.sort(key=lambda x: str(x.get("time") or ""))
    return fills, None


def _exec_time_to_dt(ts: str) -> datetime | None:
    raw = str(ts).strip()
    if not raw:
        return None
    return iso_timestamp_to_utc(raw)


def _buy_side(side: str) -> bool:
    u = (side or "").upper().strip()
    return u in ("BOT", "BUY")


def _sell_side(side: str) -> bool:
    u = (side or "").upper().strip()
    return u in ("SLD", "SELL")


def match_fills_to_local_trades(
    local_rows: list[TradeLedgerRecord],
    fills_by_order: dict[int, list[dict[str, Any]]],
    *,
    positions_by_symbol: dict[str, float] | None,
    open_orders_by_id: dict[int, dict[str, Any]] | None,
) -> list["ReconciledTrade"]:
    """Match execution rows + local ledger rows."""

    trades: list[ReconciledTrade] = []

    pos_map = positions_by_symbol or {}
    oo_map = open_orders_by_id or {}

    for rec in local_rows:
        skips = rec.raw_json.get("skipped_reasons") or []
        if isinstance(skips, list) and any(str(x).strip() for x in skips):
            continue
        if rec.raw_json.get("submitted_to_broker"):
            sent = True
        elif rec.raw_json.get("submitted"):
            sent = True
        else:
            continue

        po = rec.parent_entry_order_id
        sl_id = rec.stop_order_id
        tp_id = rec.target_order_id

        oid_fills = _collect_order_fills(po, sl_id, tp_id, fills_by_order)

        sym = rec.symbol.upper()
        direction = str(rec.direction or "").lower().strip()
        qty_plan = abs(float(rec.qty)) if rec.qty is not None else None

        entry_candidates: list[dict[str, Any]] = []
        if po is not None:
            entry_candidates = list(fills_by_order.get(int(po), []))

        exit_stop = (
            fills_by_order.get(int(sl_id), []) if sl_id is not None else []
        )
        exit_tgt = (
            fills_by_order.get(int(tp_id), []) if tp_id is not None else []
        )
        exits = sorted(
            [_dict_by_exec(x) for x in exit_stop + exit_tgt],
            key=lambda x: str(x.get("time") or ""),
        )

        unmatched_reason = ""

        entry_filled = (
            [_dict_by_exec(x) for x in entry_candidates]
            if entry_candidates
            else []
        )

        recon = ReconciledTrade(
            trade_id=rec.trade_id,
            symbol=sym,
            direction=direction or "unknown",
            strategy=str(rec.strategy or ""),
            mode=str(rec.mode_signal or ""),
            local_submitted_time=rec.submitted_time,
            parent_order_id=po,
            stop_order_id=sl_id,
            target_order_id=tp_id,
            planned_entry_price=rec.entry_price,
            planned_stop_price=rec.stop_price,
            planned_target_price=rec.target_price,
            planned_qty=qty_plan,
            raw_local=dict(rec.raw_json),
        )

        if not oid_fills and not exit_stop and not exit_tgt:
            recon.status = TradeReconStatus.submitted_not_filled
            if sent:
                pass
            else:
                recon.status = TradeReconStatus.unknown
            if not entry_filled:
                recon.broker_position_confirmed = abs(pos_map.get(sym, 0.0)) > 1e-9
                if recon.broker_position_confirmed and entry_filled == []:
                    unmatched_reason += "position_without_matching_order_ids;"
            trades.append(recon._finalize(unmatched_reason))
            continue

        if entry_filled:
            et_agg = _aggregate_fills(entry_filled)
            recon.entry_fill_time = et_agg["first_time"]
            recon.entry_fill_price = et_agg["vwap_price"]
            recon.entry_filled_qty = et_agg["qty"]

        exits_agg = exits
        realized_close_reason = CloseReason.not_closed

        if entry_filled and exits_agg:
            ex_agg = _aggregate_fills(exits_agg)
            recon.exit_fill_time = ex_agg["last_time"]
            recon.exit_fill_price = ex_agg["vwap_price"]
            recon.exit_filled_qty = ex_agg["qty"]
            oid_first = exits_agg[-1].get("order_id")
            if oid_first is not None:
                oid_i = int(oid_first)
                if tp_id is not None and oid_i == int(tp_id):
                    realized_close_reason = CloseReason.target_hit
                elif sl_id is not None and oid_i == int(sl_id):
                    realized_close_reason = CloseReason.stop_hit
                else:
                    realized_close_reason = CloseReason.manual
            recon.close_reason = realized_close_reason
            recon.status = TradeReconStatus.closed

            qty_ref = qty_plan if qty_plan else recon.entry_filled_qty
            rr, pusd = compute_realized_metrics(
                direction,
                recon.entry_fill_price or 0.0,
                recon.exit_fill_price or 0.0,
                qty_ref,
                recon.planned_stop_price,
            )
            recon.realized_r = rr
            recon.realized_pnl_usd = pusd

        elif entry_filled and not exits_agg:
            recon.status = TradeReconStatus.filled_open
            recon.close_reason = CloseReason.not_closed
            qty_ref = qty_plan if qty_plan else recon.entry_filled_qty
            recon.realized_r = None
            recon.realized_pnl_usd = None
        elif not entry_filled and (exit_stop or exit_tgt):
            recon.status = TradeReconStatus.unknown
            unmatched_reason += "exit_fill_without_parent_entry_odds;"
        else:
            recon.status = TradeReconStatus.submitted_not_filled

        if qty_plan is not None and recon.entry_filled_qty is not None:
            if abs(abs(recon.entry_filled_qty) - abs(qty_plan)) > max(1e-6, 0.2 * qty_plan):
                if recon.status == TradeReconStatus.closed:
                    recon.status = TradeReconStatus.partially_filled

        oo_parent = oo_map.get(int(po)) if po is not None else None
        recon.open_order_confirmed = bool(oo_parent)
        trades.append(recon._finalize(unmatched_reason))

    return trades


class TradeReconStatus:
    submitted_not_filled = "submitted_not_filled"
    filled_open = "filled_open"
    closed = "closed"
    partially_filled = "partially_filled"
    cancelled = "cancelled"
    rejected = "rejected"
    unknown = "unknown"


class CloseReason:
    target_hit = "target_hit"
    stop_hit = "stop_hit"
    manual = "manual"
    eod = "eod"
    unknown = "unknown"
    not_closed = "not_closed"


@dataclass
class ReconciledTrade:
    trade_id: str
    symbol: str
    direction: str
    strategy: str
    mode: str
    local_submitted_time: str
    parent_order_id: int | None
    stop_order_id: int | None
    target_order_id: int | None
    planned_entry_price: float | None
    planned_stop_price: float | None
    planned_target_price: float | None
    planned_qty: float | None
    local_order_ids: list[int | None] = field(default_factory=list)
    entry_fill_time: str | None = None
    entry_fill_price: float | None = None
    entry_filled_qty: float | None = None
    exit_fill_time: str | None = None
    exit_fill_price: float | None = None
    exit_filled_qty: float | None = None
    status: str = TradeReconStatus.unknown
    close_reason: str = CloseReason.unknown
    realized_r: float | None = None
    realized_pnl_usd: float | None = None
    broker_position_confirmed: bool = False
    open_order_confirmed: bool = False
    reconciliation_time: str = ""
    reconcile_note: str = ""
    raw_fills_sample: list[dict[str, Any]] = field(default_factory=list)
    raw_local: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _finalize(self, note_extra: str) -> ReconciledTrade:
        self.reconcile_note = (note_extra + self.reconcile_note).strip(";")
        return self


def _dict_by_exec(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d[k] for k in d}


def _collect_order_fills(
    po: int | None,
    sl: int | None,
    tp: int | None,
    fills_by_order: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for oid in (po, sl, tp):
        if oid is None:
            continue
        oid_i = int(oid)
        out.extend(fills_by_order.get(oid_i, []))
    return out


def _filter_entry_fills(
    pooled: list[dict[str, Any]],
    direction: str,
    fills_by_order: dict[int, list[dict[str, Any]]],
    po: int | None,
) -> list[dict[str, Any]]:
    if po is not None:
        return list(fills_by_order.get(int(po), []))
    pooled_u = pooled[:]
    out: list[dict[str, Any]] = []
    for ef in pooled_u:
        s = ef.get("side") or ""
        if direction == "long" and _buy_side(s):
            out.append(ef)
        elif direction == "short" and _sell_side(s):
            out.append(ef)
    return out


def _aggregate_fills(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"first_time": None, "last_time": None, "vwap_price": None, "qty": 0.0}
    times = sorted([str(r.get("time") or "") for r in rows if r.get("time")])

    qty = sum(abs(float(r.get("quantity") or 0)) for r in rows)
    notional = sum(
        abs(float(r.get("quantity") or 0)) * abs(float(r.get("price") or 0))
        for r in rows
    )
    px = notional / qty if qty > 1e-12 else None
    return {
        "first_time": times[0] if times else None,
        "last_time": times[-1] if times else None,
        "vwap_price": float(px) if px is not None else None,
        "qty": float(qty),
    }


def compute_realized_metrics(
    direction: str,
    entry_p: float,
    exit_p: float | None,
    qty_abs: float | None,
    stop_plan: float | None,
) -> tuple[float | None, float | None]:
    """R multiple vs planned stop; approximate USD P/L."""

    if exit_p is None or qty_abs is None:
        return None, None
    d = direction.lower().strip()
    qty = abs(float(qty_abs))
    if qty < 1e-12:
        return None, None
    try:
        ex = float(exit_p)
        st = float(stop_plan) if stop_plan is not None else None
        if d == "short":
            if st is not None:
                risk = st - entry_p
                reward = entry_p - ex
                rr = reward / risk if abs(risk) > 1e-12 else None
            else:
                rr = None
            usd = qty * (entry_p - ex)
            return rr, round(usd, 8)

        if st is not None:
            risk = entry_p - st
            reward = ex - entry_p
            rr = reward / risk if abs(risk) > 1e-12 else None
        else:
            rr = None
        usd = qty * (ex - entry_p)
        return rr, round(usd, 8)
    except (ArithmeticError, TypeError, ValueError):
        return None, None


def _fills_index(fills: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    by_oid: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for f in fills:
        oid = f.get("order_id")
        if oid is None:
            continue
        try:
            by_oid[int(oid)].append(f)
        except (TypeError, ValueError):
            continue
    return dict(by_oid)


def reconcile_fills_cli(
    project_root: Path,
    *,
    session_date: date | None,
    latest: bool,
    symbols: list[str] | None,
    dry_run: bool,
    write_disk: bool,
) -> dict[str, Any]:
    """Main entrypoint for ``python -m bot.cli reconcile-fills``."""

    from .config import load_config

    root = Path(project_root).resolve()
    cfg = load_config(project_root=root)

    if latest and session_date is None:
        session_date = date.fromisoformat(_ny_today_iso())

    from .trade_ledger import build_trade_records

    records = build_trade_records(root, apply_fill_reconciliation=False)
    filters = {s.strip().upper() for s in (symbols or []) if s.strip()}
    rs = records
    if filters:
        rs = [r for r in records if r.symbol.upper() in filters]
    ny_date = session_date or date.fromisoformat(_ny_today_iso())
    rs_day = []
    for r in rs:
        d = trade_reports_day_from_ts(r.submitted_time)
        if d == ny_date:
            rs_day.append(r)

    sym_set = filters if filters else None
    fills_raw, snap_err = fetch_ibkr_executions_for_reconciliation(
        cfg=cfg,
        date_only=session_date if session_date else None,
        symbols=sym_set,
    )

    meta_pos: dict[str, float] = {}
    oo_by_id: dict[int, dict[str, Any]] = {}
    if snap_err is None:
        outcome = connect_readonly_roster_retry(cfg, "broker_readonly")
        if outcome.client is not None:
            broker = Broker(cfg, outcome.client, None)
            try:
                for p in broker.get_positions():
                    d = (
                        p.to_dict()
                        if hasattr(p, "to_dict")
                        else asdict(p)
                    )
                    sym = str(d.get("symbol") or "").upper()
                    qty = float(d.get("position") or d.get("quantity") or 0)
                    meta_pos[sym] = qty
                for oo in broker.get_open_orders():
                    oo_d = oo.to_dict() if hasattr(oo, "to_dict") else {}
                    oid = oo_d.get("order_id") or oo_d.get("orderId")
                    if oid is not None:
                        try:
                            oo_by_id[int(oid)] = oo_d if isinstance(oo_d, dict) else {}
                        except (TypeError, ValueError):
                            pass
            except Exception:
                pass
            try:
                outcome.client.disconnect()
            except Exception:
                pass

    by_oid = _fills_index([dict(x) for x in fills_raw])
    matched = match_fills_to_local_trades(
        rs_day,
        by_oid,
        positions_by_symbol=meta_pos,
        open_orders_by_id=oo_by_id,
    )

    now = _utc_now_iso()
    for m in matched:
        m.reconciliation_time = now
    fills_count = len(fills_raw)
    summary = summarize_trades([m.to_dict() for m in matched], ny_date.isoformat())

    executions_path = root / EXEC_DAY.format(date=ny_date.isoformat())
    recon_day_path = root / RECON_DAY.format(date=ny_date.isoformat())

    payload_last = {
        "reconciled_at_utc": now,
        "date": ny_date.isoformat(),
        "fills_count": fills_count,
        "local_trade_candidates": len(rs_day),
        "local_trade_count": len(rs_day),
        "submitted_not_filled_count": summary["submitted_not_filled_count"],
        "filled_open_count": summary["filled_open_count"],
        "closed_count": summary["closed_count"],
        "partial_count": summary["partial_count"],
        "unknown_count": summary["unknown_count"],
        "realized_r_total": summary["realized_r_total"],
        "realized_pnl_usd_total": summary["realized_pnl_usd_total"],
        "errors": ([snap_err] if snap_err else []),
        "trades": [m.to_dict() for m in matched],
        "broker_snapshot_hint": meta_pos if meta_pos else None,
    }

    if write_disk and not dry_run:
        executions_path.parent.mkdir(parents=True, exist_ok=True)
        recon_day_path.parent.mkdir(parents=True, exist_ok=True)
        executions_path.write_text(
            json.dumps(
                {"date": ny_date.isoformat(), "fills": fills_raw},
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        recon_day_path.write_text(
            json.dumps(
                {
                    "date": ny_date.isoformat(),
                    "trades": [m.to_dict() for m in matched],
                    "summary": summary,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        lst = fills_reconciliation_last_path(root)
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text(
            json.dumps(payload_last, indent=2, ensure_ascii=False, default=str)
            + "\n",
            encoding="utf-8",
        )

    summary["fills_count"] = fills_count
    summary["reconciled_at_utc"] = now
    summary["local_trade_candidates"] = len(rs_day)
    summary["warnings"] = [snap_err] if snap_err else []
    summary["persisted_paths"] = {
        "last": RUNTIME_LAST,
        **(
            {}
            if dry_run or not write_disk
            else {
                "executions": str(executions_path.relative_to(root)),
                "daily_reconciled_trades": str(recon_day_path.relative_to(root)),
            }
        ),
    }
    summary["errors"] = [snap_err] if snap_err else []
    return summary


def trade_reports_day_from_ts(ts: str | None) -> date | None:
    if not ts:
        return None
    try:
        s = ts.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s[:32]).astimezone(_NY).date()
    except ValueError:
        return None


def summarize_trades(trade_dicts: list[dict[str, Any]], _date_str: str) -> dict[str, Any]:
    sn = fo = cl = pf = unk = 0
    rr_sum = 0.0
    r_n = 0
    usd_sum = 0.0
    u_count = 0
    for t in trade_dicts:
        st = t.get("status") or ""
        if st == TradeReconStatus.submitted_not_filled:
            sn += 1
        elif st == TradeReconStatus.filled_open:
            fo += 1
        elif st == TradeReconStatus.closed:
            cl += 1
        elif st == TradeReconStatus.partially_filled:
            pf += 1
        elif st == TradeReconStatus.unknown:
            unk += 1
        rr = t.get("realized_r")
        if rr is not None:
            rr_sum += float(rr)
            r_n += 1
        u = t.get("realized_pnl_usd")
        if u is not None:
            usd_sum += float(u)
            u_count += 1
    return {
        "date": _date_str,
        "submitted_not_filled_count": sn,
        "filled_open_count": fo,
        "closed_count": cl,
        "partial_count": pf,
        "unknown_count": unk,
        "realized_r_total": rr_sum,
        "realized_r_closed_samples": r_n,
        "realized_pnl_usd_total": usd_sum if u_count else None,
    }


def merge_reconciliation_into_trade_payload(
    project_root: Path, trade_id: str, obj: dict[str, Any]
) -> dict[str, Any]:
    """Overlay reconciled entry/exit fills on a paper-order JSON payload (for charts)."""

    m = trade_reconciliation_map(project_root)
    t = m.get((trade_id or "").strip().lower())
    if not t:
        return obj
    out = dict(obj)
    est = str(t.get("status") or "")
    out["_recon_status"] = est
    if t.get("entry_fill_price") is not None:
        out["entry"] = float(t["entry_fill_price"])
    if t.get("entry_fill_time"):
        out["entry_fill_time"] = str(t["entry_fill_time"])[:32]
    if est == "closed":
        if t.get("exit_fill_price") is not None:
            out["exit_price"] = float(t["exit_fill_price"])
        if t.get("exit_fill_time"):
            out["exit_time"] = str(t["exit_fill_time"])[:32]
    elif est in ("filled_open", "submitted_not_filled"):
        out.pop("exit_time", None)
        out.pop("exit_price", None)
    return out


def load_fills_reconciliation_last(project_root: Path | str) -> dict[str, Any] | None:
    p = fills_reconciliation_last_path(project_root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def trade_reconciliation_map(project_root: Path | str) -> dict[str, dict[str, Any]]:
    """trade_id → reconciled dict from last reconciliation run."""

    blob = load_fills_reconciliation_last(project_root)
    if not blob:
        return {}
    trades = blob.get("trades") or []
    out: dict[str, dict[str, Any]] = {}
    for t in trades:
        tid = str(t.get("trade_id") or "").strip().lower()
        if len(tid) >= 16 and isinstance(t, dict):
            out[tid] = t
    return out


def apply_reconciliation_to_records(records: list[TradeLedgerRecord], project_root: Path) -> None:
    mmap = trade_reconciliation_map(project_root)
    if not mmap:
        return

    for r in records:
        tr = mmap.get(r.trade_id.strip().lower())
        if not tr:
            continue
        setattr(r, "fill_reconciliation", tr)

        est = str(tr.get("status") or "")
        # Prefer broker-confirmed exits for display fields — never synthesize exits.
        if tr.get("entry_fill_price") is not None:
            r.entry_price = float(tr["entry_fill_price"])  # type: ignore[arg-type]
        if tr.get("entry_fill_time"):
            r.entry_time = str(tr["entry_fill_time"])[:32]

        if est == TradeReconStatus.closed:
            if tr.get("exit_fill_price") is not None:
                r.exit_price = float(tr["exit_fill_price"])  # type: ignore[arg-type]
            if tr.get("exit_fill_time"):
                r.exit_time = str(tr["exit_fill_time"])[:32]
            r.status_slug = "closed"
            if tr.get("realized_r") is not None:
                r.realized_r = float(tr["realized_r"])
        elif est == TradeReconStatus.filled_open:
            r.status_slug = "open"
            r.exit_time = None
            r.exit_price = None
            r.realized_r = None
        elif est == TradeReconStatus.submitted_not_filled:
            r.status_slug = "pending"
            r.realized_r = None
        elif est == TradeReconStatus.partially_filled:
            r.status_slug = "partial"
        elif est == TradeReconStatus.unknown:
            r.status_slug = "reconciliation_unknown"

        crc = str(tr.get("close_reason") or "").strip().lower().replace("/", "_") or ""
        if crc in (
            CloseReason.target_hit,
            CloseReason.stop_hit,
            CloseReason.manual,
            CloseReason.eod,
            CloseReason.unknown,
        ):
            r.close_reason = crc
        elif crc == CloseReason.not_closed:
            pass

