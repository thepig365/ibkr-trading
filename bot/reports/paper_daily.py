"""File-based daily paper trading report (Prompt 13M). No IBKR connection."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import load_config
from ..edge.ticker_edge import (
    REC_DISABLED,
    REC_STRICT_AND_AGGRESSIVE,
    REC_STRICT_ONLY,
    REC_WATCH_ONLY,
)
from .report_paths import (
    INTRADAY_SMC_DIR,
    PAPER_ORDERS_DIR,
    RESEARCH_DIR,
    RUNTIME_FIRST_PASS,
    RUNTIME_INTRADAY_FLAG,
    RUNTIME_LOOP_STATE,
    KILL_SWITCH,
    backtest_summary_latest,
    edge_profile_path_for_date,
    research_files_for_date,
    safe_read_json,
    safe_read_jsonl,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_runtime_flag(path: Path) -> bool | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw in {"1", "true", "True", "on", "ON"}:
        return True
    if raw in {"0", "false", "False", "off", "OFF"}:
        return False
    return None


def _sum_notional_submitted(rows: list[dict[str, Any]]) -> float:
    s = 0.0
    for row in rows:
        if not row.get("submitted_to_broker"):
            continue
        n = row.get("estimated_notional")
        if n is None:
            try:
                n = float(row.get("quantity") or 0) * float(row.get("entry") or 0)
            except (TypeError, ValueError):
                n = 0.0
        s += float(n or 0.0)
    return s


def _estimate_risk_usd(row: dict[str, Any]) -> float:
    try:
        e = float(row.get("entry") or 0)
        st = float(row.get("stop") or 0)
        q = int(row.get("quantity") or 0)
        return abs(e - st) * max(q, 0)
    except (TypeError, ValueError):
        return 0.0


def _collect_skip_reasons(rows: list[dict[str, Any]], extra: list[str]) -> list[str]:
    out: list[str] = []
    for row in rows:
        for r in row.get("skipped_reasons") or []:
            if r:
                out.append(str(r))
    out.extend(extra)
    return out


def _top_reasons(reasons: list[str], n: int = 8) -> list[dict[str, Any]]:
    c = Counter(reasons)
    return [{"reason": k, "count": v} for k, v in c.most_common(n)]


def _parse_edge_profiles_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {
            "profiles_count": 0,
            "top_edge_symbols": [],
            "disabled_symbols": [],
            "watch_only_symbols": [],
            "strict_only_symbols": [],
            "strict_and_aggressive_symbols": [],
            "edge_file": None,
        }
    data = safe_read_json(path) or {}
    profs: list[dict[str, Any]] = list(data.get("profiles") or [])
    disabled, watch, strict, both = [], [], [], []
    top_syms: list[str] = []
    for p in profs:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").upper()
        if not sym:
            continue
        rec = str(p.get("recommended_mode") or "")
        if rec == REC_DISABLED:
            disabled.append(sym)
        elif rec == REC_WATCH_ONLY:
            watch.append(sym)
        elif rec == REC_STRICT_ONLY:
            strict.append(sym)
        elif rec == REC_STRICT_AND_AGGRESSIVE:
            both.append(sym)
        sc = p.get("edge_score")
        try:
            sc_f = float(sc) if sc is not None else 0.0
        except (TypeError, ValueError):
            sc_f = 0.0
        top_syms.append((sc_f, sym))
    top_syms.sort(key=lambda x: -x[0])
    return {
        "profiles_count": len(profs),
        "top_edge_symbols": [s for _, s in top_syms[:15]],
        "disabled_symbols": disabled,
        "watch_only_symbols": watch,
        "strict_only_symbols": strict,
        "strict_and_aggressive_symbols": both,
        "edge_file": str(path),
    }


def _execution_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    broker_err_codes: list[int] = []
    n_sub_broker = 0
    n_submitted = 0
    n_complete = 0
    n_incomplete = 0
    n_not = 0
    n_skipped = 0
    notionals = 0.0
    risk_sum = 0.0
    for row in rows:
        to_b = bool(row.get("submitted_to_broker"))
        sub = bool(row.get("submitted"))
        bint = str(row.get("bracket_integrity") or "")
        if to_b:
            n_sub_broker += 1
        if sub:
            n_submitted += 1
        if bint == "complete" and to_b:
            n_complete += 1
        elif bint in {"incomplete", "unknown"} and to_b:
            n_incomplete += 1
        if not to_b and bint in {"", "not_submitted"}:
            n_not += 1
        sr = row.get("skipped_reasons") or []
        if sr:
            n_skipped += 1
        for c in row.get("broker_error_codes") or []:
            try:
                broker_err_codes.append(int(c))
            except (TypeError, ValueError):
                pass
        if to_b or sub:
            notionals += float(row.get("estimated_notional") or 0.0) or 0.0
            if not row.get("estimated_notional"):
                try:
                    notionals += float(row.get("quantity") or 0) * float(row.get("entry") or 0)
                except (TypeError, ValueError):
                    pass
        if to_b:
            risk_sum += _estimate_risk_usd(row)
    return {
        "paper_orders_count": len(rows),
        "submitted_to_broker_count": n_sub_broker,
        "submitted_count": n_submitted,
        "complete_bracket_count": n_complete,
        "incomplete_bracket_count": n_incomplete,
        "not_submitted_count": n_not,
        "skipped_count": n_skipped,
        "broker_error_codes": sorted(set(broker_err_codes)),
        "submitted_notional_total": round(notionals, 2),
        "estimated_risk_total": round(risk_sum, 2),
    }


def _scan_from_summary(scan: dict[str, Any] | None) -> dict[str, Any]:
    if not scan:
        return {
            "symbols_scanned": 0,
            "strict_ready_count": 0,
            "aggressive_ready_count": 0,
            "watch_only_count": 0,
            "invalid_risk_count": 0,
            "blocked_count": 0,
            "no_setup_count": 0,
            "error_count": 0,
            "ready_symbols": [],
            "ready_strict_symbols": [],
            "ready_aggressive_symbols": [],
            "watch_symbols": [],
        }
    counts = dict(scan.get("counts") or {})
    st = "DAY_TRADE_READY_STRICT"
    ag = "DAY_TRADE_READY_AGGRESSIVE"
    wo = "WATCH_ONLY"
    ir = "INVALID_RISK"
    bl = "BLOCKED"
    ns = "NO_SETUP"
    er = "ERROR"
    rs = list(scan.get("ready_strict_symbols") or [])
    ra = list(scan.get("ready_aggressive_symbols") or [])
    return {
        "symbols_scanned": int(scan.get("symbols_scanned") or 0),
        "strict_ready_count": int(counts.get(st, 0)),
        "aggressive_ready_count": int(counts.get(ag, 0)),
        "watch_only_count": int(counts.get(wo, 0)),
        "invalid_risk_count": int(counts.get(ir, 0)),
        "blocked_count": int(counts.get(bl, 0)),
        "no_setup_count": int(counts.get(ns, 0)),
        "error_count": int(counts.get(er, 0)),
        "ready_symbols": list(dict.fromkeys([*rs, *ra])),
        "ready_strict_symbols": rs,
        "ready_aggressive_symbols": ra,
        "watch_symbols": list(scan.get("watch_symbols") or []),
    }


def _performance_from_backtest(path: Path | None) -> dict[str, Any | None]:
    if path is None or not path.is_file():
        return {
            "realized_pnl": None,
            "total_r": None,
            "win_rate": None,
            "average_r": None,
            "open_positions_count": None,
            "backtest_summary_path": None,
        }
    data = safe_read_json(path) or {}
    m = data.get("metrics") or {}
    return {
        "realized_pnl": None,
        "total_r": m.get("total_r"),
        "win_rate": m.get("win_rate"),
        "average_r": m.get("average_r"),
        "open_positions_count": None,
        "backtest_summary_path": str(path),
    }


def build_daily_paper_report(project_root: Path, report_date: str) -> dict[str, Any]:
    """Assemble the DailyPaperReport as a JSON-serialisable dict. No network."""
    root = Path(project_root)
    generated_at = _now_iso()
    cfg = load_config(project_root=root)
    ip = cfg.settings.trading.intraday_paper
    acct = cfg.settings.account
    # --- file inputs
    source_files: dict[str, str | None] = {}
    loop_path = root / RUNTIME_LOOP_STATE
    loop = safe_read_json(loop_path) or {}
    source_files["loop_state"] = str(loop_path) if loop_path.is_file() else None

    fpp_path = root / RUNTIME_FIRST_PASS
    fpp = safe_read_json(fpp_path) or {}
    source_files["first_paper_pass_last"] = str(fpp_path) if fpp_path.is_file() else None

    porders_path = root / PAPER_ORDERS_DIR / f"{report_date}-intraday-paper-orders.jsonl"
    rows = safe_read_jsonl(porders_path)
    source_files["paper_orders"] = str(porders_path) if porders_path.is_file() else None

    scan_path = root / INTRADAY_SMC_DIR / f"{report_date}-watchlist-intraday-smc-summary.json"
    scan = safe_read_json(scan_path)
    source_files["scan_summary"] = str(scan_path) if scan_path.is_file() else None

    rp, ri = research_files_for_date(root, report_date)
    source_files["research_report"] = str(rp) if rp and rp.is_file() else None
    source_files["research_instructions"] = str(ri) if ri and ri.is_file() else None
    if (root / RESEARCH_DIR).is_dir():
        source_files["research_dir"] = str(root / RESEARCH_DIR)

    edge_p = edge_profile_path_for_date(root, report_date)
    edge_info = _parse_edge_profiles_file(edge_p)

    bt_p = backtest_summary_latest(root)
    perf = _performance_from_backtest(bt_p)
    if bt_p and bt_p.is_file():
        source_files["backtest_summary"] = str(bt_p)

    # --- engine / runtime
    kill_path = root / KILL_SWITCH
    kill = False
    if kill_path.is_file():
        try:
            kill = bool(kill_path.read_text().strip())
        except OSError:
            kill = False
    ri_flag = _read_runtime_flag(root / RUNTIME_INTRADAY_FLAG)
    ri_on: bool | None
    if "runtime_intraday_on" in loop and loop.get("runtime_intraday_on") is not None:
        ri_on = bool(loop.get("runtime_intraday_on"))
    else:
        ri_on = ri_flag
    if ri_on is None:
        ri_on = bool(fpp.get("runtime_intraday_on")) if fpp.get("runtime_intraday_on") is not None else False
    engine_status = {
        "runtime_intraday_on": ri_on,
        "kill_switch": bool(
            kill or loop.get("kill_switch") or fpp.get("kill_switch_active")
        ),
        "config_enabled": bool(loop.get("config_enabled", ip.enabled)),
        "fully_automatic": bool(loop.get("fully_automatic", ip.fully_automatic)),
    }

    # budget
    max_d = float(ip.max_daily_notional_usd)
    used = _sum_notional_submitted(rows)
    remaining = max(0.0, max_d - used)
    daily_cap_reached = used >= max_d - 1e-6
    budget = {
        "max_notional_per_order_usd": float(ip.max_notional_per_order_usd),
        "max_daily_notional_usd": max_d,
        "today_submitted_notional_usd": round(used, 2),
        "daily_remaining_notional_usd": round(remaining, 2),
    }

    scan_summary = _scan_from_summary(scan)
    exec_sum = _execution_from_rows(rows)
    from_loop_skip = list(loop.get("skipped_reasons") or [])

    reasons_list = _collect_skip_reasons(rows, from_loop_skip)
    top_skipped = _top_reasons(reasons_list)
    fpp_blockers = fpp.get("reasons")
    if isinstance(fpp_blockers, str):
        fpp_blockers = [fpp_blockers]
    readiness_blockers: list[str] = []
    if isinstance(fpp_blockers, list):
        readiness_blockers = [str(x) for x in fpp_blockers if x]
    for x in fpp.get("blocking_reasons") or []:  # type: ignore[assignment]
        if x:
            readiness_blockers.append(str(x))
    if not scan and not rows:
        readiness_blockers.append("no scan summary and no paper orders for this date")

    no_signal = (scan_summary["strict_ready_count"] + scan_summary["aggressive_ready_count"]) == 0
    no_trade_reasons = list({r["reason"] for r in top_skipped}) if top_skipped else []
    if no_signal and "no ready signals in scan" not in no_trade_reasons:
        no_trade_reasons.append("no strict/aggressive ready signals in scan")

    fpp_ict: dict[str, bool | None] = {
        "five_min_setup_found": None,
        "one_min_trigger_found": None,
        "higher_timeframe_context_ok": None,
    }
    ict_skipped_hints = 0
    if fpp and isinstance(fpp.get("execution"), dict):
        for sub in (fpp.get("execution") or {}).get("submissions") or []:
            if not isinstance(sub, dict):
                continue
            it = sub.get("intent")
            if isinstance(it, dict):
                if fpp_ict["five_min_setup_found"] is None and "five_min_setup_found" in it:
                    fpp_ict["five_min_setup_found"] = bool(it.get("five_min_setup_found"))
    for row in rows:
        blob = " ".join(str(s) for s in (row.get("skipped_reasons") or []))
        if "one_min" in blob.lower() or "1m" in blob.lower() or "ict" in blob.lower():
            ict_skipped_hints += 1

    sources_present = {k: v is not None for k, v in source_files.items()}
    partial = not (source_files.get("paper_orders") or source_files.get("scan_summary"))
    if not any(sources_present.values()):
        data_status: str = "no_data"
    elif partial and not rows and not scan:
        data_status = "partial_data"
    else:
        data_status = "ok" if (rows or scan or loop) else "partial_data"

    safety = {
        "live_trading_allowed": bool(ip.live_trading_allowed),
        "market_orders_allowed": bool(ip.market_orders_allowed),
        "bracket_required": bool(ip.bracket_required),
        "stop_required": bool(ip.stop_required),
        "target_required": bool(ip.target_required),
        "reconcile_status": (loop.get("reconciliation_status") or loop.get("reconcile_status") or fpp.get("reconcile")) if (loop or fpp) else None,
        "unprotected_position_warning": any(
            (not r.get("submitted")) and r.get("submitted_to_broker")
            and str(r.get("bracket_integrity") or "") != "complete"
            for r in rows
        ),
    }

    next_action = "Review scan and edge profiles; ensure ICT chain + runtime ON when you intend to paper trade."
    if daily_cap_reached:
        next_action = "Wait for the next session or monitor daily notional; caps are not reset by this report."
    elif exec_sum["incomplete_bracket_count"] > 0:
        next_action = "Verify incomplete brackets in TWS; cancel or fix protected legs before continuing."
    elif data_status in {"no_data", "partial_data"}:
        next_action = "Run intraday watchlist scan and / or first-paper-flow inputs to populate artifacts (no orders in this report)."

    report: dict[str, Any] = {
        "date": report_date,
        "generated_at": generated_at,
        "paper_only": bool(ip.paper_only),
        "account_mode": getattr(acct, "mode", None),
        "data_status": data_status,
        "source_files": source_files,
        "engine_status": engine_status,
        "budget": budget,
        "scan_summary": scan_summary,
        "edge_summary": {k: v for k, v in edge_info.items() if k != "edge_file"},
        "edge_file": edge_info.get("edge_file"),
        "execution_summary": exec_sum,
        "reasons": {
            "top_skipped_reasons": top_skipped,
            "no_trade_reasons": no_trade_reasons,
            "daily_cap_reached": daily_cap_reached,
            "no_signal": no_signal,
            "readiness_blockers": list(dict.fromkeys(readiness_blockers)),
        },
        "ict_chain_validation": fpp_ict,
        "ict_chain_skip_reason_hints": ict_skipped_hints,
        "performance": {
            "realized_pnl": perf["realized_pnl"],
            "total_r": perf.get("total_r"),
            "win_rate": perf.get("win_rate"),
            "average_r": perf.get("average_r"),
            "open_positions_count": perf.get("open_positions_count"),
        },
        "safety": safety,
        "next_actions": {
            "suggested_next_step": next_action,
            "notes": "File-based; no IBKR. Does not modify caps or ledgers.",
        },
    }
    if bt_p and bt_p.is_file():
        report["performance"]["backtest_summary_path"] = str(bt_p)
    rj: dict[str, Any] = {}
    if rp and rp.is_file():
        try:
            rj = json.loads(rp.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            rj = {}
    report["research_context"] = {
        "headline": rj.get("headline") or rj.get("summary"),
        "path": str(rp) if rp and rp.is_file() else None,
    }
    return report
