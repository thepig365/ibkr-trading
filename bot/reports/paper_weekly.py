"""Aggregate weekly paper trading report (Prompt 13M). File-based; no IBKR."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paper_daily import build_daily_paper_report
from .report_paths import daterange_inclusive, parse_date, PAPER_ORDERS_DIR, safe_read_jsonl


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_week_from_latest(_project_root: Path) -> tuple[date, date]:
    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=6)
    return start_d, end_d


def _ticker_stats_for_day(
    project_root: Path, day: str
) -> dict[str, dict[str, Any]]:
    p = project_root / PAPER_ORDERS_DIR / f"{day}-intraday-paper-orders.jsonl"
    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "orders": 0,
            "complete_brackets": 0,
            "r_sum": 0.0,
            "r_n": 0,
            "edge_scores": list[float](),
        }
    )
    for row in safe_read_jsonl(p):
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        st = by_sym[sym]
        st["orders"] += 1
        if str(row.get("bracket_integrity") or "") == "complete" and row.get("submitted_to_broker"):
            st["complete_brackets"] += 1
        ea = row.get("edge_audit")
        if isinstance(ea, dict) and ea.get("edge_score") is not None:
            try:
                st["edge_scores"].append(float(ea["edge_score"]))
            except (TypeError, ValueError):
                pass
        elif isinstance(ea, dict) and isinstance(ea.get("profile"), dict):
            sc = (ea.get("profile") or {}).get("edge_score")
            if sc is not None:
                try:
                    st["edge_scores"].append(float(sc))
                except (TypeError, ValueError):
                    pass
        pr = row.get("planned_rr")
        if pr is not None:
            try:
                st["r_sum"] += float(pr)
                st["r_n"] += 1
            except (TypeError, ValueError):
                pass
    for _sym, st in by_sym.items():
        scores = st.get("edge_scores") or []
        st["edge_score"] = (sum(scores) / len(scores)) if scores else None
        st["avg_planned_rr"] = (st["r_sum"] / st["r_n"]) if st["r_n"] else None
    return dict(by_sym)


def build_weekly_paper_report(
    project_root: Path,
    week_start: str,
    week_end: str,
) -> dict[str, Any]:
    root = Path(project_root)
    d0, d1 = parse_date(week_start), parse_date(week_end)
    if d0 > d1:
        d0, d1 = d1, d0
    days = [d.isoformat() for d in daterange_inclusive(d0, d1)]
    dailies: list[dict[str, Any]] = [build_daily_paper_report(root, d) for d in days]

    trading_days = len(days)
    total_scanned = 0
    total_ready = 0
    total_orders = 0
    total_sub_broker = 0
    total_complete = 0
    total_incomplete = 0
    total_notional = 0.0
    cap_days = 0
    no_signal_days = 0
    blockers: Counter[str] = Counter()
    from_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "signals": 0,
            "orders": 0,
            "complete_brackets": 0,
            "r_values": list[float](),
            "edge_scores": list[float](),
        }
    )

    for d_iso, day_rep in zip(days, dailies):
        sc = day_rep.get("scan_summary") or {}
        total_scanned += int(sc.get("symbols_scanned") or 0)
        total_ready += int(sc.get("strict_ready_count") or 0) + int(
            sc.get("aggressive_ready_count") or 0
        )
        ex = day_rep.get("execution_summary") or {}
        total_orders += int(ex.get("paper_orders_count") or 0)
        total_sub_broker += int(ex.get("submitted_to_broker_count") or 0)
        total_complete += int(ex.get("complete_bracket_count") or 0)
        total_incomplete += int(ex.get("incomplete_bracket_count") or 0)
        total_notional += float(ex.get("submitted_notional_total") or 0.0)
        rs = sc.get("ready_symbols") or []
        for sym in rs:
            s = str(sym).upper()
            if s:
                from_sym[s]["signals"] += 1
        st_day = _ticker_stats_for_day(root, d_iso)
        for sym, st in st_day.items():
            bucket = from_sym[sym]
            bucket["orders"] += st["orders"]
            bucket["complete_brackets"] += st["complete_brackets"]
            if st.get("avg_planned_rr") is not None and st.get("r_n", 0) > 0:
                n = int(st.get("r_n") or 0)
                for _ in range(n):
                    bucket["r_values"].append(float(st["avg_planned_rr"]))
            if st.get("edge_score") is not None:
                bucket["edge_scores"].append(float(st["edge_score"]))

        rsn = (day_rep.get("reasons") or {})
        if rsn.get("daily_cap_reached"):
            cap_days += 1
        if rsn.get("no_signal"):
            no_signal_days += 1
        for item in rsn.get("top_skipped_reasons") or []:
            if isinstance(item, dict) and item.get("reason"):
                blockers[str(item["reason"])] += int(item.get("count") or 0)
        for b in rsn.get("readiness_blockers") or []:
            blockers[str(b)] += 1

    total_r: float | None = None
    avg_r: float | None = None
    m_tr: list[float] = []
    m_wr: list[float] = []
    for dr in dailies:
        p = (dr.get("performance") or {})
        if p.get("total_r") is not None:
            try:
                m_tr.append(float(p["total_r"]))
            except (TypeError, ValueError):
                pass
        if p.get("average_r") is not None:
            try:
                m_wr.append(float(p["average_r"]))
            except (TypeError, ValueError):
                pass
    if m_tr:
        total_r = sum(m_tr)
    if m_wr:
        avg_r = sum(m_wr) / len(m_wr)

    day_breakdown = [
        {
            "date": d_iso,
            "scan_symbols": (dr.get("scan_summary") or {}).get("symbols_scanned"),
            "orders": (dr.get("execution_summary") or {}).get("paper_orders_count"),
            "skipped_top": (dr.get("reasons") or {}).get("top_skipped_reasons"),
            "daily_cap_reached": (dr.get("reasons") or {}).get("daily_cap_reached"),
        }
        for d_iso, dr in zip(days, dailies)
    ]

    def _r(block: str) -> int:
        return int(blockers.get(block, 0))

    recurring = {
        "no_signal": no_signal_days,
        "daily_cap_reached": cap_days,
        "edge_blocked": _r("edge") + _r("aggressive")
        + _r("edge_profile") + _r("AGGRESSIVE"),
        "ict_chain_missing": _r("one_min")
        + _r("ICT")
        + _r("1m")
        + _r("5m"),
        "broker_error": _r("broker")
        + _r("Error")
        + _r("TWS")
        + _r("10349")
        + _r("110"),
        "tws_timeout": _r("timeout") + _r("RequestTimeout") + _r("disconnected"),
    }

    over_restricted = no_signal_days >= max(1, len(days) // 2)
    sizing_blocks = cap_days >= 1
    keep_tickers: list[str] = []
    need_data: list[str] = []
    for sym, b in sorted(
        from_sym.items(),
        key=lambda x: (x[1].get("signals", 0) + x[1].get("orders", 0)),
        reverse=True,
    )[:12]:
        if b["orders"] and b.get("complete_brackets", 0) >= 1:
            keep_tickers.append(sym)
        elif b.get("signals", 0) and not b.get("orders"):
            need_data.append(sym)

    recommendations = {
        "tickers_to_keep": keep_tickers,
        "tickers_need_more_data": need_data[:20],
        "over_restricted_signal_flow": over_restricted,
        "sizing_caps_likely_blocking": sizing_blocks,
        "loop_safe_to_continue": not (
            (dailies[-1].get("safety") or {}).get("unprotected_position_warning")
        )
        and recurring["broker_error"] < 3,
    }

    return {
        "week_start": d0.isoformat(),
        "week_end": d1.isoformat(),
        "generated_at": _now_iso(),
        "trading_days_count": trading_days,
        "total_symbols_scanned": total_scanned,
        "total_ready_signals": total_ready,
        "total_paper_orders": total_orders,
        "total_submitted_to_broker": total_sub_broker,
        "total_complete_brackets": total_complete,
        "total_incomplete_brackets": total_incomplete,
        "total_notional": round(total_notional, 2),
        "total_r": total_r,
        "avg_r": avg_r,
        "ticker_breakdown": {
            k: {
                "signals": v["signals"],
                "orders": v["orders"],
                "complete_brackets": v["complete_brackets"],
                "avg_r": (sum(v["r_values"]) / len(v["r_values"])) if v["r_values"] else None,
                "edge_score": (sum(v["edge_scores"]) / len(v["edge_scores"]))
                if v["edge_scores"]
                else None,
            }
            for k, v in sorted(from_sym.items(), key=lambda x: -x[1]["orders"])
        },
        "day_breakdown": day_breakdown,
        "recurring_blockers": recurring,
        "recommendations": recommendations,
    }


def build_weekly_latest(project_root: Path) -> dict[str, Any]:
    a, b = _parse_week_from_latest(project_root)
    return build_weekly_paper_report(project_root, a.isoformat(), b.isoformat())
