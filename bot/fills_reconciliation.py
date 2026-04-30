"""Read-only IBKR fills reconciliation vs local Strategy Lab paper order rows.

Loads TWS executions via the existing read-only roster (``broker_readonly``);
never places, cancels, or modifies orders. Persists summaries under ``data/runtime/``
and per-day archives — these paths must not be committed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

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
    orf = getattr(r, "order_ref", None)
    return {
        "exec_id": getattr(r, "exec_id", "") or "",
        "order_id": getattr(r, "order_id", None),
        "perm_id": getattr(r, "perm_id", None),
        "symbol": (getattr(r, "symbol", "") or "").upper(),
        "sec_type": (getattr(r, "sec_type", "") or "").upper(),
        "side": getattr(r, "side", "") or "",
        "quantity": float(getattr(r, "shares", 0) or 0),
        "price": float(getattr(r, "price", 0) or 0),
        "time": getattr(r, "time", None),
        "account": getattr(r, "account", "") or "",
        "order_ref": (str(orf).strip() if orf else "") or "",
        "raw": d,
    }


def fetch_ibkr_executions_for_reconciliation(
    *,
    cfg: "AppConfig | None" = None,
    date_only: date | None = None,
    date_ny_start: date | None = None,
    date_ny_end: date | None = None,
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
        elif date_ny_start is not None and date_ny_end is not None and tstr:
            dt = _exec_time_to_dt(tstr)
            if dt is None:
                continue
            d_ny = dt.astimezone(_NY).date()
            if not (date_ny_start <= d_ny <= date_ny_end):
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


def _stk_execution_dict(f: dict[str, Any]) -> bool:
    st = str(f.get("sec_type") or "").upper().strip()
    return st in ("", "STK")


def _exec_fingerprint(f: dict[str, Any]) -> str:
    eid = str(f.get("exec_id") or "").strip()
    if eid:
        return f"id:{eid}"
    return (
        "synth:"
        f"{f.get('order_id')}|{f.get('perm_id')}|{f.get('time')}|{f.get('side')}|"
        f"{f.get('quantity')}|{f.get('price')}|{f.get('symbol')}"
    )


def _mark_fills_used(fills: Iterable[dict[str, Any]], used: set[str]) -> None:
    for fx in fills:
        used.add(_exec_fingerprint(fx))


def _make_order_perm_fill_lookup(fills_list: list[dict[str, Any]]):
    by_oid: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_perm: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for f in fills_list:
        oid = f.get("order_id")
        if oid is not None:
            try:
                by_oid[int(oid)].append(f)
            except (TypeError, ValueError):
                pass
        pid = f.get("perm_id")
        if pid is not None:
            try:
                by_perm[int(pid)].append(f)
            except (TypeError, ValueError):
                pass

    def lookup_leg(order_id: int | None) -> list[dict[str, Any]]:
        if order_id is None:
            return []
        oi = int(order_id)
        seen_fp: set[str] = set()
        out_l: list[dict[str, Any]] = []
        for src in (by_oid.get(oi, ()), by_perm.get(oi, ())):
            for row in src:
                fp = _exec_fingerprint(row)
                if fp in seen_fp:
                    continue
                seen_fp.add(fp)
                out_l.append(row)
        return out_l

    return lookup_leg


def _collect_leg_fills_lookup(
    po: int | None,
    sl: int | None,
    tp: int | None,
    lookup_leg: Any,
) -> list[dict[str, Any]]:
    out_union: list[dict[str, Any]] = []
    seen: set[str] = set()
    for oid in (po, sl, tp):
        if oid is None:
            continue
        for f in lookup_leg(int(oid)):
            fp = _exec_fingerprint(f)
            if fp in seen:
                continue
            seen.add(fp)
            out_union.append(f)
    return out_union


def _pair_exit_fills_from_unused(
    direction: str,
    unused_sym_fills: list[dict[str, Any]],
    *,
    after_time: str | None,
    qty_need: float | None,
) -> list[dict[str, Any]]:
    if qty_need is None or qty_need < 1e-9:
        return []
    lo = direction.lower().strip()
    want_exit_buy = lo == "short"
    side_ok = _buy_side if want_exit_buy else _sell_side
    pool = sorted(unused_sym_fills, key=lambda x: str(x.get("time") or ""))
    cand: list[dict[str, Any]] = []
    for f in pool:
        if not side_ok(str(f.get("side") or "")):
            continue
        t = str(f.get("time") or "")
        if after_time and t and t <= str(after_time):
            continue
        cand.append(f)
    tol = max(1e-6, float(qty_need) * 0.02)
    acc = 0.0
    picked: list[dict[str, Any]] = []
    for f in cand:
        q = abs(float(f.get("quantity") or 0))
        if q < 1e-12:
            continue
        picked.append(f)
        acc += q
        if acc + 1e-9 >= float(qty_need) - tol:
            break
    if abs(acc - float(qty_need)) <= max(tol, 0.5):
        return picked
    if acc >= float(qty_need) * 0.85:
        return picked
    return []


def _pair_entry_exit_round_trip_unused(
    direction: str,
    unused_sym_fills: list[dict[str, Any]],
    anchor: datetime | None,
    qty_plan: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    target_qty_hint = abs(float(qty_plan)) if qty_plan is not None and qty_plan > 1e-9 else None
    pool = sorted(unused_sym_fills, key=lambda x: str(x.get("time") or ""))
    lo = direction.lower().strip()
    open_side_ok = _buy_side if lo == "long" else _sell_side
    close_side_ok = _sell_side if lo == "long" else _buy_side
    tol_ratio = 0.12
    margin = timedelta(hours=8)
    first_i: int | None = None
    for i, f in enumerate(pool):
        if not open_side_ok(str(f.get("side") or "")):
            continue
        tstr = str(f.get("time") or "")
        dt = _exec_time_to_dt(tstr)
        if anchor is not None and dt is not None and dt < anchor - margin:
            continue
        first_i = i
        break
    if first_i is None:
        return None

    opening: list[dict[str, Any]] = []
    target_qty = target_qty_hint
    acc_o = 0.0
    for j in range(first_i, len(pool)):
        f = pool[j]
        if not open_side_ok(str(f.get("side") or "")):
            break
        q = abs(float(f.get("quantity") or 0))
        if q < 1e-12:
            continue
        opening.append(f)
        acc_o += q
        if target_qty is not None and acc_o >= target_qty * (1.0 - tol_ratio):
            break
        if target_qty is None and acc_o > 1e-9:
            target_qty = acc_o
            break

    if not opening or target_qty is None:
        return None

    last_open_t = str(opening[-1].get("time") or "")
    close_pool = [f for f in pool if str(f.get("time") or "") > last_open_t]
    closing: list[dict[str, Any]] = []
    acc_c = 0.0
    for f in close_pool:
        if not close_side_ok(str(f.get("side") or "")):
            continue
        q = abs(float(f.get("quantity") or 0))
        closing.append(f)
        acc_c += q
        if acc_c + 1e-9 >= target_qty * (1.0 - tol_ratio):
            break

    tol = max(1e-6, target_qty * tol_ratio)
    if not closing or abs(acc_c - float(target_qty)) > max(tol, 1.0):
        return None
    return opening, closing


def _apply_flat_position_symbol_fallback(
    trades: list[ReconciledTrade],
    fills_list: list[dict[str, Any]],
    pos_map: dict[str, float],
    used_fps: set[str],
) -> None:
    order = sorted(range(len(trades)), key=lambda ix: str(trades[ix].local_submitted_time or ""))
    for ix in order:
        recon = trades[ix]
        sym_u = recon.symbol.upper()
        if abs(float(pos_map.get(sym_u, 0.0))) > 1e-8:
            continue
        if recon.status not in (
            TradeReconStatus.filled_open,
            TradeReconStatus.submitted_not_filled,
        ):
            continue

        sym_unused: list[dict[str, Any]] = []
        for x in fills_list:
            if not _stk_execution_dict(x):
                continue
            if str(x.get("symbol") or "").upper() != sym_u:
                continue
            if _exec_fingerprint(x) in used_fps:
                continue
            sym_unused.append(x)
        sym_unused.sort(key=lambda z: str(z.get("time") or ""))

        if recon.status == TradeReconStatus.filled_open:
            qty_ref = recon.planned_qty or recon.entry_filled_qty
            et = recon.entry_fill_time
            if not qty_ref or not et:
                continue
            exits2 = _pair_exit_fills_from_unused(
                recon.direction,
                sym_unused,
                after_time=str(et),
                qty_need=float(qty_ref),
            )
            if exits2:
                ex_agg = _aggregate_fills(exits2)
                recon.exit_fill_time = ex_agg["last_time"]
                recon.exit_fill_price = ex_agg["vwap_price"]
                recon.exit_filled_qty = ex_agg["qty"]
                recon.close_reason = CloseReason.manual
                recon.status = TradeReconStatus.closed
                qty_r = float(qty_ref)
                rr, pusd = compute_realized_metrics(
                    recon.direction,
                    float(recon.entry_fill_price or 0.0),
                    float(recon.exit_fill_price or 0.0),
                    qty_r,
                    recon.planned_stop_price,
                )
                recon.realized_r = rr
                recon.realized_pnl_usd = pusd
                note = "flat_symbol_exit_from_unmatched_executions"
                recon.reconcile_note = (
                    f"{recon.reconcile_note};{note}" if recon.reconcile_note else note
                )
                _mark_fills_used(exits2, used_fps)
            continue

        anchor = _exec_time_to_dt(recon.local_submitted_time or "")
        pair = _pair_entry_exit_round_trip_unused(
            recon.direction,
            sym_unused,
            anchor,
            recon.planned_qty,
        )
        if not pair:
            continue
        opening, closing = pair
        eo = _aggregate_fills(opening)
        xc = _aggregate_fills(closing)
        recon.entry_fill_time = eo["first_time"]
        recon.entry_fill_price = eo["vwap_price"]
        recon.entry_filled_qty = eo["qty"]
        recon.exit_fill_time = xc["last_time"]
        recon.exit_fill_price = xc["vwap_price"]
        recon.exit_filled_qty = xc["qty"]
        recon.close_reason = CloseReason.unknown
        recon.status = TradeReconStatus.closed
        qty_r2 = float(recon.planned_qty or recon.entry_filled_qty or 0.0)
        rr2, pusd2 = compute_realized_metrics(
            recon.direction,
            float(recon.entry_fill_price or 0.0),
            float(recon.exit_fill_price or 0.0),
            qty_r2,
            recon.planned_stop_price,
        )
        recon.realized_r = rr2
        recon.realized_pnl_usd = pusd2
        note2 = "flat_symbol_round_trip_from_unmatched_executions"
        recon.reconcile_note = (
            f"{recon.reconcile_note};{note2}" if recon.reconcile_note else note2
        )
        _mark_fills_used(opening + closing, used_fps)


def match_fills_to_local_trades(
    local_rows: list[TradeLedgerRecord],
    fills_list: list[dict[str, Any]],
    *,
    positions_by_symbol: dict[str, float] | None,
    open_orders_by_id: dict[int, dict[str, Any]] | None,
    used_exec_fingerprints: set[str],
) -> list[ReconciledTrade]:
    """Match execution rows + local ledger rows."""

    lookup_leg = _make_order_perm_fill_lookup(fills_list)

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

        oid_fills = _collect_leg_fills_lookup(po, sl_id, tp_id, lookup_leg)

        sym = rec.symbol.upper()
        direction = str(rec.direction or "").lower().strip()
        qty_plan = abs(float(rec.qty)) if rec.qty is not None else None

        entry_candidates: list[dict[str, Any]] = []
        if po is not None:
            entry_candidates = list(lookup_leg(int(po)))

        exit_stop = list(lookup_leg(int(sl_id))) if sl_id is not None else []
        exit_tgt = list(lookup_leg(int(tp_id))) if tp_id is not None else []
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
            recon.reconcile_note = unmatched_reason.strip(";").strip(";")
            trades.append(recon)
            continue

        if entry_filled:
            et_agg = _aggregate_fills(entry_filled)
            recon.entry_fill_time = et_agg["first_time"]
            recon.entry_fill_price = et_agg["vwap_price"]
            recon.entry_filled_qty = et_agg["qty"]
            _mark_fills_used(entry_filled, used_exec_fingerprints)

        exits_agg = exits

        if entry_filled and exits_agg:
            ex_agg = _aggregate_fills(exits_agg)
            recon.exit_fill_time = ex_agg["last_time"]
            recon.exit_fill_price = ex_agg["vwap_price"]
            recon.exit_filled_qty = ex_agg["qty"]
            _mark_fills_used(exits_agg, used_exec_fingerprints)
            oid_last = exits_agg[-1].get("order_id")
            realized_close_reason = CloseReason.manual
            if oid_last is not None:
                oid_i = int(oid_last)
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
            recon.realized_r = None
            recon.realized_pnl_usd = None
        elif not entry_filled and (exit_stop or exit_tgt):
            recon.status = TradeReconStatus.unknown
            unmatched_reason += "exit_fill_without_parent_entry_odds;"
            _mark_fills_used(exit_stop + exit_tgt, used_exec_fingerprints)
        else:
            recon.status = TradeReconStatus.submitted_not_filled

        if qty_plan is not None and recon.entry_filled_qty is not None:
            if abs(abs(recon.entry_filled_qty) - abs(qty_plan)) > max(1e-6, 0.2 * qty_plan):
                if recon.status == TradeReconStatus.closed:
                    recon.status = TradeReconStatus.partially_filled

        oo_parent = oo_map.get(int(po)) if po is not None else None
        recon.open_order_confirmed = bool(oo_parent)
        recon.reconcile_note = unmatched_reason.strip(";").strip(";")
        trades.append(recon)

    _apply_flat_position_symbol_fallback(
        trades,
        fills_list,
        pos_map,
        used_exec_fingerprints,
    )

    for t in trades:
        t.reconcile_note = str(t.reconcile_note or "").strip(";")
        t._finalize()

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


def _bucket_close_reason_slug(slug: str) -> str:
    """UI bucket: target / stop / manual / unknown."""

    c = str(slug or "").strip().lower()
    if c == CloseReason.target_hit:
        return "target"
    if c == CloseReason.stop_hit:
        return "stop"
    if c in (CloseReason.manual, CloseReason.eod):
        return "manual"
    return "unknown"


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
    close_reason_bucket: str = "unknown"
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

    def _finalize(self) -> ReconciledTrade:
        if self.status == TradeReconStatus.closed:
            self.close_reason_bucket = _bucket_close_reason_slug(self.close_reason)
        else:
            self.close_reason_bucket = "unknown"
        return self


def _dict_by_exec(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d[k] for k in d}


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


def reconcile_fills_cli(
    project_root: Path,
    *,
    session_date: date | None,
    latest: bool,
    symbols: list[str] | None,
    dry_run: bool,
    write_disk: bool,
    ledger_ny_range: tuple[date, date] | None = None,
    executions_ny_range: tuple[date, date] | None = None,
    notify_telegram_newly_closed: bool = False,
    generate_closed_trade_charts: bool = False,
) -> dict[str, Any]:
    """Main entrypoint for ``python -m bot.cli reconcile-fills`` / stock reconcile."""

    from .config import load_config

    root = Path(project_root).resolve()
    cfg = load_config(project_root=root)

    if latest and session_date is None:
        session_date = date.fromisoformat(_ny_today_iso())

    from .broker_snapshot import load_broker_snapshot
    from .trade_ledger import build_trade_records

    records = build_trade_records(root, apply_fill_reconciliation=False)
    filters = {s.strip().upper() for s in (symbols or []) if s.strip()}
    rs = records
    if filters:
        rs = [r for r in records if r.symbol.upper() in filters]

    ny_today_d = date.fromisoformat(_ny_today_iso())
    if ledger_ny_range is not None:
        ledger_lo, ledger_hi = ledger_ny_range
        rs_day = []
        for r in rs:
            d_sub = trade_reports_day_from_ts(r.submitted_time)
            if d_sub is not None and ledger_lo <= d_sub <= ledger_hi:
                rs_day.append(r)
        ny_date_key = ledger_hi
    else:
        ny_date_key = session_date or ny_today_d
        rs_day = []
        for r in rs:
            d = trade_reports_day_from_ts(r.submitted_time)
            if d == ny_date_key:
                rs_day.append(r)

    sym_set = filters if filters else None
    if executions_ny_range is not None:
        fills_raw, snap_err = fetch_ibkr_executions_for_reconciliation(
            cfg=cfg,
            date_only=None,
            date_ny_start=executions_ny_range[0],
            date_ny_end=executions_ny_range[1],
            symbols=sym_set,
        )
    else:
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

    used_fps: set[str] = set()
    matched = match_fills_to_local_trades(
        rs_day,
        [dict(x) for x in fills_raw],
        positions_by_symbol=meta_pos,
        open_orders_by_id=oo_by_id,
        used_exec_fingerprints=used_fps,
    )

    unmatched_exec_rows: list[dict[str, Any]] = []
    for f in fills_raw:
        if not _stk_execution_dict(f):
            continue
        if _exec_fingerprint(f) in used_fps:
            continue
        unmatched_exec_rows.append(
            {
                "kind": "broker_execution_no_local_trade",
                "message": "Broker execution not matched to local trade.",
                "exec_id": f.get("exec_id"),
                "symbol": f.get("symbol"),
                "side": f.get("side"),
                "quantity": f.get("quantity"),
                "price": f.get("price"),
                "time": f.get("time"),
                "order_id": f.get("order_id"),
                "perm_id": f.get("perm_id"),
                "order_ref": f.get("order_ref"),
                "account": f.get("account"),
            }
        )

    now = _utc_now_iso()
    for m in matched:
        m.reconciliation_time = now

    fills_count = len(fills_raw)
    summary_date = ny_date_key.isoformat()
    if ledger_ny_range is not None:
        summary_date = (
            f"{ledger_ny_range[0].isoformat()}:{ledger_ny_range[1].isoformat()}"
        )
    summary = summarize_trades([m.to_dict() for m in matched], summary_date)

    prev_blob = load_fills_reconciliation_last(root)
    prev_status: dict[str, str] = {}
    if isinstance(prev_blob, dict):
        for t in prev_blob.get("trades") or []:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("trade_id") or "").strip().lower()
            if tid:
                prev_status[tid] = str(t.get("status") or "")
    newly_closed: list[dict[str, Any]] = []
    for m in matched:
        if str(m.status) != TradeReconStatus.closed:
            continue
        tid = str(m.trade_id).strip().lower()
        if prev_status.get(tid) != TradeReconStatus.closed:
            newly_closed.append(
                {
                    "trade_id": tid,
                    "symbol": m.symbol,
                    "entry_fill_price": m.entry_fill_price,
                    "exit_fill_price": m.exit_fill_price,
                    "realized_pnl_usd": m.realized_pnl_usd,
                    "status": m.status,
                }
            )

    bsnap = load_broker_snapshot(root) or {}
    bmeta = bsnap.get("meta") if isinstance(bsnap.get("meta"), dict) else {}
    acct_metrics = (
        bmeta.get("account_metrics")
        if isinstance(bmeta.get("account_metrics"), dict)
        else {}
    )
    broker_rpnl = acct_metrics.get("realized_pnl")
    local_rpnl = summary.get("realized_pnl_usd_total")
    pnl_mismatch = False
    if broker_rpnl is not None and local_rpnl is not None:
        try:
            pnl_mismatch = abs(float(broker_rpnl) - float(local_rpnl)) > 0.51
        except (TypeError, ValueError):
            pnl_mismatch = True

    executions_path = root / EXEC_DAY.format(date=ny_date_key.isoformat())
    recon_day_path = root / RECON_DAY.format(date=ny_date_key.isoformat())

    payload_last = {
        "reconciled_at_utc": now,
        "date": ny_date_key.isoformat(),
        "ledger_ny_range": (
            {
                "start": ledger_ny_range[0].isoformat(),
                "end": ledger_ny_range[1].isoformat(),
            }
            if ledger_ny_range
            else None
        ),
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
        "broker_snapshot_realized_pnl_usd": broker_rpnl,
        "broker_vs_local_realized_pnl_mismatch": pnl_mismatch,
        "errors": ([snap_err] if snap_err else []),
        "trades": [m.to_dict() for m in matched],
        "unmatched_broker_executions": unmatched_exec_rows,
        "broker_snapshot_hint": meta_pos if meta_pos else None,
        "newly_closed_trades": newly_closed,
    }

    if write_disk and not dry_run:
        executions_path.parent.mkdir(parents=True, exist_ok=True)
        recon_day_path.parent.mkdir(parents=True, exist_ok=True)
        executions_path.write_text(
            json.dumps(
                {"date": ny_date_key.isoformat(), "fills": fills_raw},
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
                    "date": ny_date_key.isoformat(),
                    "trades": [m.to_dict() for m in matched],
                    "summary": summary,
                    "unmatched_broker_executions": unmatched_exec_rows,
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

        if generate_closed_trade_charts and newly_closed:
            from .journal_trade_charts_pipeline import (  # noqa: PLC0415
                ensure_trade_chart_if_possible,
            )

            for blk in newly_closed:
                tidc = str(blk.get("trade_id") or "").strip().lower()
                if len(tidc) >= 16:
                    ensure_trade_chart_if_possible(root, tidc, force=False)

        if notify_telegram_newly_closed and newly_closed:
            from .journal import Journal  # noqa: PLC0415
            from .notifications import send_telegram_message  # noqa: PLC0415

            lines = ["Strategy Lab · reconcile closed (broker fills)"]
            for blk in newly_closed[:24]:
                lines.append(
                    f"{blk.get('symbol')} entry={blk.get('entry_fill_price')} "
                    f"exit={blk.get('exit_fill_price')} "
                    f"pnlUSD={blk.get('realized_pnl_usd')} "
                    "status=closed"
                )
            try:
                send_telegram_message(
                    "\n".join(lines),
                    cfg=cfg,
                    journal=Journal(cfg),
                )
            except Exception:
                pass

    summary["fills_count"] = fills_count
    summary["reconciled_at_utc"] = now
    summary["ledger_ny_range"] = payload_last.get("ledger_ny_range")
    summary["local_trade_candidates"] = len(rs_day)
    summary["unmatched_broker_executions_count"] = len(unmatched_exec_rows)
    summary["broker_snapshot_realized_pnl_usd"] = broker_rpnl
    summary["broker_vs_local_realized_pnl_mismatch"] = pnl_mismatch
    summary["newly_closed_trades"] = newly_closed
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

        bk = str(tr.get("close_reason_bucket") or "").strip().lower()
        if bk == "target" and est == TradeReconStatus.closed:
            r.close_reason = "target_hit"
        elif bk == "stop" and est == TradeReconStatus.closed:
            r.close_reason = "stop_hit"
        elif bk == "manual" and est == TradeReconStatus.closed:
            r.close_reason = "manual"

