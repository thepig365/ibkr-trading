"""Read-only summaries for the Reports UI hub (no IBKR, no network).

Structured excerpts from JSON/MD on disk; never dump full large payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .report_paths import (
    DEFAULT_REPORT_DIR,
    EDGE_DIR,
    backtest_summary_latest,
    latest_glob_path,
    safe_read_json,
)


def _read_head_lines(path: Path, max_lines: int = 14) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[:max_lines])


def _compact_paper_daily(data: dict[str, Any]) -> dict[str, Any]:
    ex = data.get("execution_summary") or {}
    b = data.get("budget") or {}
    sfty = data.get("safety") or {}
    perf = data.get("performance") or {}
    return {
        "date": data.get("date"),
        "paper_only": data.get("paper_only"),
        "data_status": data.get("data_status"),
        "submitted_to_broker_count": ex.get("submitted_to_broker_count"),
        "submitted_count": ex.get("submitted_count"),
        "complete_bracket_count": ex.get("complete_bracket_count"),
        "incomplete_bracket_count": ex.get("incomplete_bracket_count"),
        "skipped_count": ex.get("skipped_count"),
        "submitted_notional_total": b.get("today_submitted_notional_usd"),
        "daily_remaining_notional_usd": b.get("daily_remaining_notional_usd"),
        "max_daily_notional_usd": b.get("max_daily_notional_usd"),
        "reconcile_status": sfty.get("reconcile_status"),
        "unprotected_position_warning": sfty.get("unprotected_position_warning"),
        "win_rate": perf.get("win_rate"),
        "total_r": perf.get("total_r"),
        "average_r": perf.get("average_r"),
    }


def _compact_backtest(data: dict[str, Any]) -> dict[str, Any]:
    m = data.get("metrics") or {}
    sym = data.get("symbols") or data.get("symbol_list")
    if isinstance(sym, str):
        sym = [s.strip() for s in sym.split(",") if s.strip()]
    if not isinstance(sym, list):
        sym = None
    ntr = m.get("total_trades")
    if ntr is None and isinstance(m.get("by_symbol"), list):
        ntr = sum(int(x.get("trades") or 0) for x in m["by_symbol"] if isinstance(x, dict))
    return {
        "run_id": data.get("run_id") or data.get("id"),
        "symbols": sym[:20] if sym else None,
        "total_trades": ntr,
        "win_rate": m.get("win_rate"),
        "average_r": m.get("average_r"),
        "total_r": m.get("total_r"),
        "profit_factor": m.get("profit_factor"),
        "max_drawdown": m.get("max_drawdown") or m.get("max_drawdown_r"),
    }


def _compact_weekly(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "week_start": data.get("week_start"),
        "week_end": data.get("week_end"),
        "trading_days_count": data.get("trading_days_count"),
        "total_paper_orders": data.get("total_paper_orders"),
        "total_submitted_to_broker": data.get("total_submitted_to_broker"),
        "total_complete_brackets": data.get("total_complete_brackets"),
        "total_notional": data.get("total_notional"),
    }


def _edge_top_rows(data: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    profs = list(data.get("profiles") or [])
    out: list[dict[str, Any]] = []
    for p in profs:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").upper()
        if not sym:
            continue
        out.append(
            {
                "symbol": sym,
                "recommended_mode": p.get("recommended_mode"),
                "confidence": p.get("confidence") or p.get("edge_confidence") or p.get("edge_score"),
            }
        )
        if len(out) >= limit:
            break
    return out


def _first_pass_compact(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "at_utc": raw.get("at_utc") or raw.get("timestamp"),
        "ok": raw.get("ok") if "ok" in raw else raw.get("success"),
        "reasons": (raw.get("reasons") or raw.get("blocking_reasons") or [])[:5]
        if isinstance(raw.get("reasons") or raw.get("blocking_reasons"), list)
        else None,
    }


def build_report_hub_ui_context(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    rpaper = root / DEFAULT_REPORT_DIR

    latest_daily_json = latest_glob_path(rpaper, "*-paper-daily-report.json")
    paper_compact: dict[str, Any] | None = None
    daily_md_excerpt = ""
    if latest_daily_json and latest_daily_json.is_file():
        dj = safe_read_json(latest_daily_json)
        if isinstance(dj, dict):
            paper_compact = _compact_paper_daily(dj)
        md = latest_daily_json.with_suffix(".md")
        daily_md_excerpt = _read_head_lines(md, 16)

    latest_weekly = latest_glob_path(rpaper, "*-paper-weekly-report.json")
    weekly_compact: dict[str, Any] | None = None
    weekly_md_excerpt = ""
    if latest_weekly and latest_weekly.is_file():
        wj = safe_read_json(latest_weekly)
        if isinstance(wj, dict):
            weekly_compact = _compact_weekly(wj)
        weekly_md_excerpt = _read_head_lines(latest_weekly.with_suffix(".md"), 12)

    bt_path = backtest_summary_latest(root)
    bt_compact: dict[str, Any] | None = None
    if bt_path and bt_path.is_file():
        bj = safe_read_json(bt_path)
        if isinstance(bj, dict):
            bt_compact = _compact_backtest(bj)

    fpp = root / "data/runtime/first_paper_pass_last.json"
    first_pass: dict[str, Any] | None = None
    fpp_raw = safe_read_json(fpp) if fpp.is_file() else None
    if isinstance(fpp_raw, dict):
        first_pass = _first_pass_compact(fpp_raw)

    latest_edge = latest_glob_path(root / EDGE_DIR, "*-edge-profiles.json")
    edge_top: list[dict[str, Any]] = []
    if latest_edge and latest_edge.is_file():
        ej = safe_read_json(latest_edge) or {}
        if isinstance(ej, dict):
            edge_top = _edge_top_rows(ej, limit=8)

    return {
        "hub_paper_daily": paper_compact,
        "hub_paper_daily_json_relpath": _rel(root, latest_daily_json),
        "hub_paper_daily_md_excerpt": daily_md_excerpt,
        "hub_paper_weekly": weekly_compact,
        "hub_paper_weekly_relpath": _rel(root, latest_weekly),
        "hub_paper_weekly_md_excerpt": weekly_md_excerpt,
        "hub_backtest": bt_compact,
        "hub_backtest_relpath": _rel(root, bt_path),
        "hub_first_paper_pass": first_pass,
        "hub_edge_top": edge_top,
        "hub_edge_relpath": _rel(root, latest_edge),
    }


def _rel(root: Path, p: Path | None) -> str | None:
    if p is None or not p.is_file():
        return None
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p)
