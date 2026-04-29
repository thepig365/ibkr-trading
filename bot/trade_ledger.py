"""Normalized trade ledger from local ``data/paper_orders`` JSONL (no broker, no IBKR).

One row ≈ one engine decision row in the audit log — presented as a trader-friendly
record where exit fields render only when present in the saved JSON object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .journal_trade_charts_pipeline import journal_chart_cell as _journal_chart_cell_ui
from .journal_trade_id import compute_stable_trade_row_id
from .journal_trade_lookup import iter_intraday_paper_order_jsonl_files
from .trade_journal_chart import candles_available_for_trade, trade_review_chart_png_path
from .ux.humanize import humanize_skip_reason


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def parse_optional_iso_ts(raw: Any) -> str | None:
    ts = _s(raw)
    if not ts:
        return None
    return ts[:32]


def ledger_status_slug(obj: dict[str, Any]) -> str:
    """Normalized status for Trade Records (UI slug)."""

    skipped = obj.get("skipped_reasons") or []
    has_skip = isinstance(skipped, list) and any(str(s).strip() for s in skipped)
    if has_skip:
        return "skipped"

    sub = bool(obj.get("submitted")) or bool(obj.get("submitted_to_broker"))
    sb_only = bool(obj.get("submitted_to_broker")) and not bool(obj.get("submitted"))
    bi = str(obj.get("bracket_integrity") or "").strip().lower()

    ex_t = parse_optional_iso_ts(obj.get("exit_time") or obj.get("exit_ts"))
    ex_p = _f(obj.get("exit_price"))
    # Trader record: closed only when both timestamp and fill price exist (never fake exits).
    full_exit = bool(ex_t) and ex_p is not None

    if full_exit:
        return "closed"

    if bi == "incomplete" and sub:
        return "protection_incomplete"

    if sb_only:
        return "pending"

    if sub and not full_exit:
        return "open"

    # Engine row but not sent to broker — treat as skipped/rejected-lite
    if not sub and has_skip:
        return "skipped"
    if not sub:
        return "rejected"
    return "unknown"


def normalized_close_reason_slug(obj: dict[str, Any], status_slug: str) -> str:
    """Buckets for UI; prefers stored engine strings when plausible."""

    ex_t = parse_optional_iso_ts(obj.get("exit_time") or obj.get("exit_ts"))
    ex_p = _f(obj.get("exit_price"))
    if status_slug != "closed" or ex_t is None or ex_p is None:
        return "not_recorded"
    raw = _s(obj.get("close_reason") or obj.get("position_close_reason")).lower()
    stop_p = _f(obj.get("stop"))
    targ_p = _f(obj.get("target"))
    if raw:
        if any(k in raw for k in ("target_hit", "take profit", "tp hit", "tp_fill", "target fill")):
            return "target_hit"
        if any(k in raw for k in ("stop_hit", "stop loss", "sl hit", "sl_fill", "stopped")):
            return "stop_hit"
        if "manual" in raw or "user" in raw:
            return "manual"
        if "eod" in raw or "end of day" in raw or "session" in raw:
            return "eod"
        if raw.strip():
            return "unknown"
    # Heuristic proximity when reason missing but we have brackets
    if stop_p is not None and targ_p is not None:
        tol = abs(stop_p - targ_p) * 0.02 + 0.02
        if abs(ex_p - targ_p) <= tol:
            return "target_hit"
        if abs(ex_p - stop_p) <= tol:
            return "stop_hit"
    return "unknown"


def format_ict_labels(obj: dict[str, Any]) -> str:
    bits: list[str] = []
    sc = _s(obj.get("signal_category"))
    if sc:
        bits.append(sc)
    for lab, ok in (
        ("HTF", obj.get("higher_timeframe_context_ok")),
        ("5m", obj.get("five_min_setup_found")),
        ("1m", obj.get("one_min_trigger_found")),
    ):
        if isinstance(ok, bool):
            bits.append(f"{lab}:{'Y' if ok else 'n'}")
    for key in ("bos", "bos_type", "mss", "liquidity_sweep", "fair_value_gap", "fvg"):
        val = obj.get(key)
        if val is None or val == "":
            continue
        bits.append(f"{key}={val}")
    return " · ".join(bits) if bits else ""


def _realized_r_guess(obj: dict[str, Any]) -> float | None:
    exit_p = _f(obj.get("exit_price"))
    entry_p = _f(obj.get("entry"))
    stop_p = _f(obj.get("stop"))
    direction = _s(obj.get("direction")).lower()
    if exit_p is None or entry_p is None or stop_p is None:
        return None
    if direction == "short":
        risk = stop_p - entry_p  # positive when stop above entry typical
        reward = entry_p - exit_p
    else:
        risk = entry_p - stop_p
        reward = exit_p - entry_p
    if risk is None or abs(risk) < 1e-9:
        return None
    return reward / risk


def edge_summary_from_payload(obj: dict[str, Any]) -> str:
    edge = obj.get("edge_audit") or obj.get("edge")
    if isinstance(edge, dict):
        es = edge.get("edge_score")
        rm = edge.get("recommended_mode")
        if es is not None or rm:
            return f"{es or '—'} · {rm or ''}".strip()
    es2 = obj.get("edge_score")
    if es2 is not None:
        return str(es2)
    return ""


@dataclass
class TradeLedgerRecord:
    trade_id: str
    symbol: str
    direction: str
    strategy: str
    mode_signal: str
    status_slug: str
    submitted_time: str
    entry_time: str | None
    entry_price: float | None
    exit_time: str | None
    exit_price: float | None
    stop_price: float | None
    target_price: float | None
    qty: float | None
    notional: float | None
    planned_rr: float | None
    realized_r: float | None
    close_reason: str
    ict_labels: str
    submitted_to_broker: bool
    skipped_reason_raw: str
    bracket_status: str
    parent_entry_order_id: int | None
    stop_order_id: int | None
    target_order_id: int | None
    ict_htf: bool | None = None
    ict_5m: bool | None = None
    ict_1m: bool | None = None
    edge_summary: str = ""
    skipped_reason_human: str = ""
    chart_status: str = ""
    chart_path: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)
    fill_reconciliation: dict[str, Any] | None = None


def effective_status_slug(rec: TradeLedgerRecord) -> str:
    """Prefer fill reconciliation status when overlay present."""

    fc = rec.fill_reconciliation
    if fc and isinstance(fc, dict):
        st = str(fc.get("status") or "")
        if st:
            return _recon_slug_to_display_slug(st)
    return rec.status_slug


def _recon_slug_to_display_slug(st: str) -> str:
    return {
        "submitted_not_filled": "submitted_not_filled",
        "filled_open": "filled_open",
        "closed": "closed",
        "partially_filled": "partially_filled",
        "unknown": "reconciliation_unknown",
    }.get(st, st)


def skipped_reason_human_for(rec: TradeLedgerRecord, locale: str = "en") -> str:
    if not rec.skipped_reason_raw:
        return ""
    return humanize_skip_reason(rec.skipped_reason_raw, locale=locale)


def hydrate_record_chart_fields(rec: TradeLedgerRecord, project_root: Path) -> TradeLedgerRecord:
    """Populate chart tier label + relative PNG path when present on disk."""

    root = Path(project_root).resolve()
    rec.chart_status = _tier_for_trade_record(rec, root)
    pn = trade_review_chart_png_path(root, rec.trade_id)
    if pn.is_file():
        try:
            rec.chart_path = str(pn.relative_to(root))
        except ValueError:
            rec.chart_path = str(pn)
    else:
        rec.chart_path = None
    return rec


def raw_dict_to_trade_record(abs_path: str, line_no: int, obj: dict[str, Any]) -> TradeLedgerRecord:
    tid = compute_stable_trade_row_id(abs_path, line_no, obj).strip().lower()
    sym = str(obj.get("symbol") or "").strip().upper()
    status = ledger_status_slug(obj)

    skips = obj.get("skipped_reasons") or []
    skips_l = skips if isinstance(skips, list) else []
    skipped_raw_first = ""
    skipped_human_en = ""
    for s in skips_l:
        if str(s).strip():
            skipped_raw_first = str(s).strip()
            skipped_human_en = humanize_skip_reason(skipped_raw_first, locale="en")
            break

    submitted_ts = _s(obj.get("timestamp") or obj.get("ts"))
    entry_t = parse_optional_iso_ts(
        obj.get("entry_fill_time") or obj.get("entry_time") or obj.get("fill_time_entry")
    )
    if not entry_t:
        entry_t = submitted_ts[:32] if submitted_ts else None

    exit_t = parse_optional_iso_ts(obj.get("exit_time") or obj.get("exit_ts"))

    cr_slug = normalized_close_reason_slug(obj, status)
    ict_lbl = format_ict_labels(obj)

    po = obj.get("parent_entry_order_id") or obj.get("entry_order_id")
    sl = obj.get("parent_sl_order_id") or obj.get("stop_order_id")
    tp = obj.get("parent_tp_order_id") or obj.get("target_order_id")

    def _iid(v: Any) -> int | None:
        try:
            if v is None:
                return None
            return int(v)
        except (TypeError, ValueError):
            return None

    return TradeLedgerRecord(
        trade_id=tid,
        symbol=sym,
        direction=_s(obj.get("direction")),
        strategy=_s(obj.get("strategy_id")),
        mode_signal=_s(obj.get("signal_category")),
        status_slug=status,
        submitted_time=submitted_ts,
        entry_time=entry_t,
        entry_price=_f(obj.get("entry")),
        exit_time=exit_t,
        exit_price=_f(obj.get("exit_price")),
        stop_price=_f(obj.get("stop")),
        target_price=_f(obj.get("target")),
        qty=_f(obj.get("quantity")),
        notional=_f(obj.get("estimated_notional")),
        planned_rr=_f(obj.get("planned_rr")),
        realized_r=_realized_r_guess(obj),
        close_reason=cr_slug,
        ict_labels=ict_lbl,
        submitted_to_broker=bool(obj.get("submitted_to_broker", False)),
        skipped_reason_raw=skipped_raw_first,
        skipped_reason_human=skipped_human_en,
        bracket_status=str(obj.get("bracket_integrity") or ""),
        parent_entry_order_id=_iid(po),
        stop_order_id=_iid(sl),
        target_order_id=_iid(tp),
        ict_htf=(
            obj.get("higher_timeframe_context_ok") if obj.get("higher_timeframe_context_ok") is not None else None
        ),
        ict_5m=obj.get("five_min_setup_found") if obj.get("five_min_setup_found") is not None else None,
        ict_1m=obj.get("one_min_trigger_found") if obj.get("one_min_trigger_found") is not None else None,
        edge_summary=edge_summary_from_payload(obj),
        raw_json=dict(obj),
        fill_reconciliation=None,
    )


def build_trade_records(
    project_root: Path,
    *,
    apply_fill_reconciliation: bool = True,
) -> list[TradeLedgerRecord]:
    """All intraday paper order rows newest-first (sorted by timestamp string descending)."""

    root = Path(project_root).resolve()
    pod = root / "data" / "paper_orders"
    if not pod.is_dir():
        return []
    out: list[tuple[str, TradeLedgerRecord]] = []
    for path in iter_intraday_paper_order_jsonl_files(pod):
        try:
            abs_p = str(path.resolve())
            with path.open(encoding="utf-8") as fh:
                for line_no, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if not _s(obj.get("symbol")):
                        continue
                    rec = raw_dict_to_trade_record(abs_p, line_no, obj)
                    ts_k = rec.submitted_time or ""
                    out.append((ts_k, rec))
        except OSError:
            continue
    out.sort(key=lambda x: x[0], reverse=True)
    rows = [r for _, r in out]
    out_rows = [hydrate_record_chart_fields(r, root) for r in rows]
    if apply_fill_reconciliation:
        from .fills_reconciliation import apply_reconciliation_to_records

        apply_reconciliation_to_records(out_rows, root)
    return out_rows


def find_trade_record(project_root: Path, trade_id: str) -> TradeLedgerRecord | None:
    want = (trade_id or "").strip().lower()
    if len(want) < 16:
        return None
    root = Path(project_root).resolve()
    pod = root / "data" / "paper_orders"
    if not pod.is_dir():
        return None
    for path in iter_intraday_paper_order_jsonl_files(pod):
        try:
            abs_p = str(path.resolve())
            with path.open(encoding="utf-8") as fh:
                for line_no, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if not _s(obj.get("symbol")):
                        continue
                    rec = raw_dict_to_trade_record(abs_p, line_no, obj)
                    if rec.trade_id == want:
                        hydrate_record_chart_fields(rec, root)
                        from .fills_reconciliation import apply_reconciliation_to_records

                        apply_reconciliation_to_records([rec], root)
                        return rec
        except OSError:
            continue
    return None


def _tier_for_trade_record(rec: TradeLedgerRecord, root: Path) -> str:
    """Reuse journal_chart_cell tier logic via a minimal namespace."""

    class _RN:
        pass

    row = _RN()
    row.trade_id = rec.trade_id
    skips = rec.raw_json.get("skipped_reasons") or []
    row.skipped_reasons = skips if isinstance(skips, list) else []
    row.submitted = bool(rec.raw_json.get("submitted"))
    row.submitted_to_broker = rec.submitted_to_broker
    row.bracket_integrity = rec.bracket_status
    row.timestamp = rec.submitted_time
    row.symbol = rec.symbol

    jc = _journal_chart_cell_ui(root, row)
    return jc.tier


def ledger_summary_counts(records: list[TradeLedgerRecord], project_root: Path) -> dict[str, int]:
    root = Path(project_root).resolve()
    submitted = skipped = incomplete = opened = closed = 0
    sent_to_broker_n = 0
    charts_ok = charts_miss = pend = na = pend_row = rej = 0
    realized_sum = 0.0
    realized_n = 0
    recon_nf = recon_fo = recon_cl = recon_pf = recon_unk = 0
    for r in records:
        if bool(r.raw_json.get("submitted")) or r.submitted_to_broker:
            submitted += 1
        if r.submitted_to_broker:
            sent_to_broker_n += 1
        if r.status_slug == "skipped":
            skipped += 1
        if r.status_slug == "protection_incomplete":
            incomplete += 1
        if r.status_slug == "open":
            opened += 1
        if r.status_slug == "closed":
            closed += 1
            if r.realized_r is not None:
                realized_sum += float(r.realized_r)
                realized_n += 1
        if r.status_slug == "pending":
            pend_row += 1
        if r.status_slug == "rejected":
            rej += 1
        fc = r.fill_reconciliation
        if isinstance(fc, dict):
            st = str(fc.get("status") or "")
            if st == "submitted_not_filled":
                recon_nf += 1
            elif st == "filled_open":
                recon_fo += 1
            elif st == "closed":
                recon_cl += 1
            elif st == "partially_filled":
                recon_pf += 1
            elif st == "unknown":
                recon_unk += 1
        tier = _tier_for_trade_record(r, root)
        if tier == "available":
            charts_ok += 1
        elif tier == "missing_candles":
            charts_miss += 1
        elif tier == "pending":
            pend += 1
        elif tier == "not_applicable":
            na += 1
        elif tier == "ready_to_draw":
            pend += 1
    return {
        "submitted_rows": submitted,
        "sent_to_broker_rows": sent_to_broker_n,
        "skipped": skipped,
        "protection_incomplete": incomplete,
        "open": opened,
        "closed": closed,
        "pending": pend_row,
        "rejected": rej,
        "charts_available": charts_ok,
        "charts_missing_candles": charts_miss,
        "charts_pending_or_ready": pend,
        "charts_na": na,
        "total": len(records),
        "realized_r_sum": realized_sum,
        "realized_r_count": realized_n,
        "recon_submitted_not_filled": recon_nf,
        "recon_filled_open": recon_fo,
        "recon_closed": recon_cl,
        "recon_partially_filled": recon_pf,
        "recon_unknown": recon_unk,
    }


def chart_png_exists(project_root: Path, trade_id: str) -> bool:
    return trade_review_chart_png_path(Path(project_root), trade_id).is_file()


def candles_ok_for_record(project_root: Path, rec: TradeLedgerRecord) -> bool:
    return candles_available_for_trade(Path(project_root), rec.raw_json)
