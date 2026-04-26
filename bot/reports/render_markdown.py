"""Markdown rendering for paper daily/weekly reports (Prompt 13M)."""

from __future__ import annotations

from typing import Any, Mapping


def render_paper_daily_markdown(
    report: Mapping[str, Any], *, project_root: Any = None
) -> str:  # noqa: ARG001
    """11-section daily report per spec (project_root unused; reserved)."""
    d = report.get("date", "?")
    lines = [f"# Paper Trading Daily Report — {d}", ""]

    es = report.get("engine_status") or {}
    bud = report.get("budget") or {}
    ex = report.get("execution_summary") or {}
    rs = report.get("reasons") or {}
    sc = report.get("scan_summary") or {}
    eg = report.get("edge_summary") or {}
    safe = report.get("safety") or {}
    perf = report.get("performance") or {}
    na = (report.get("next_actions") or {}).get("suggested_next_step", "")

    lines += [
        "## 1. Executive Summary",
        f"- **Data status:** {report.get('data_status', 'unknown')}",
        f"- **Paper only:** {report.get('paper_only', True)}  **Account mode:** {report.get('account_mode', '—')}",
        f"- **Engine:** intraday_on={es.get('runtime_intraday_on')}  kill_switch={es.get('kill_switch')}  "
        f"config={es.get('config_enabled')}  auto={es.get('fully_automatic')}",
        f"- **Orders today (audit):** {ex.get('paper_orders_count', 0)}  "
        f"submitted_to_broker={ex.get('submitted_to_broker_count', 0)}  complete_brackets={ex.get('complete_bracket_count', 0)}",
        "",
        "## 2. Safety Status",
        f"- live_trading_allowed={safe.get('live_trading_allowed')}  market_orders_allowed={safe.get('market_orders_allowed')}",
        f"- bracket / stop / target: {safe.get('bracket_required')} / {safe.get('stop_required')} / {safe.get('target_required')}",
        f"- reconcile_status: {safe.get('reconcile_status')}",
        f"- unprotected_position_warning: {safe.get('unprotected_position_warning')}",
        "",
        "## 3. Budget Usage",
        f"- Per-trade cap USD: {bud.get('max_notional_per_order_usd')}",
        f"- Daily cap USD: {bud.get('max_daily_notional_usd')}",
        f"- Submitted notional (audit): {bud.get('today_submitted_notional_usd')}",
        f"- Remaining (derived): {bud.get('daily_remaining_notional_usd')}",
        f"- **Daily cap reached (from ledger file):** {rs.get('daily_cap_reached')}",
        "",
        "## 4. Research / Market Context",
        f"- {report.get('research_context', {}) or '—'}",
        f"- Research report path: {((report.get('source_files') or {}).get('research_report')) or '—'}",
        "",
        "## 5. Scan Summary",
        f"- Symbols scanned: {sc.get('symbols_scanned', 0)}",
        f"- STRICT: {sc.get('strict_ready_count', 0)}  AGG: {sc.get('aggressive_ready_count', 0)}  "
        f"WATCH: {sc.get('watch_only_count', 0)}  INVALID: {sc.get('invalid_risk_count', 0)}  "
        f"BLOCK: {sc.get('blocked_count', 0)}  NO_SETUP: {sc.get('no_setup_count', 0)}  ERR: {sc.get('error_count', 0)}",
        f"- Ready: {', '.join((sc.get('ready_symbols') or [])[:20]) or '—'}",
        f"- Watch: {', '.join((sc.get('watch_symbols') or [])[:20]) or '—'}",
        "",
        "## 6. Edge Profile Summary",
        f"- Profiles: {eg.get('profiles_count', 0)}",
        f"- Top: {', '.join((eg.get('top_edge_symbols') or [])[:20]) or '—'}",
        f"- watch_only: {len(eg.get('watch_only_symbols') or [])}  strict_only: {len(eg.get('strict_only_symbols') or [])}  "
        f"both: {len(eg.get('strict_and_aggressive_symbols') or [])}  disabled: {len(eg.get('disabled_symbols') or [])}",
        f"- **Edge file used:** {report.get('edge_file') or (report.get('source_files') or {}).get('edge')}",
        "",
        "## 7. Paper Execution Summary",
        f"- submitted_to_broker: {ex.get('submitted_to_broker_count', 0)}  submitted(complete): {ex.get('submitted_count', 0)}  "
        f"not_submitted: {ex.get('not_submitted_count', 0)}",
        f"- Notional total: {ex.get('submitted_notional_total', 0)}  "
        f"est. risk (|entry-stop|·qty) sum: {ex.get('estimated_risk_total', 0)}",
        f"- broker_error_codes: {ex.get('broker_error_codes', [])}",
        "",
        "## 8. Bracket Integrity",
        f"- complete: {ex.get('complete_bracket_count', 0)}  incomplete: {ex.get('incomplete_bracket_count', 0)}  "
        f"rows with skip text: {ex.get('skipped_count', 0)}",
        "",
        "## 9. Why No Trade / Why Skipped",
        f"- no_signal: {rs.get('no_signal')}",
        f"- top skipped: {rs.get('top_skipped_reasons', [])}",
        f"- blockers: {rs.get('readiness_blockers', [])}",
        f"- ict validation flags: {report.get('ict_chain_validation', {})}  ict text hints: {report.get('ict_chain_skip_reason_hints', 0)}",
        "",
        "## 10. Performance (if available)",
        f"- total_r: {perf.get('total_r')}  win_rate: {perf.get('win_rate')}  avg_r: {perf.get('average_r')}",
        f"- backtest: {perf.get('backtest_summary_path') or '—'}",
        "",
        "## 11. Next Actions",
        f"- {na}",
        f"- {((report.get('next_actions') or {}).get('notes', ''))}",
        "",
    ]
    return "\n".join(lines)


def render_paper_weekly_markdown(report: Mapping[str, Any]) -> str:
    ws, we = report.get("week_start"), report.get("week_end")
    lines = [f"# Paper Trading Weekly Report — {ws} to {we}", ""]

    rb = report.get("recurring_blockers") or {}
    rec = report.get("recommendations") or {}
    tbd = report.get("ticker_breakdown") or {}

    lines += [
        "## 1. Weekly Summary",
        f"- trading_days: {report.get('trading_days_count')}  scanned: {report.get('total_symbols_scanned')}  "
        f"ready signals: {report.get('total_ready_signals')}",
        f"- paper orders: {report.get('total_paper_orders')}  to_broker: {report.get('total_submitted_to_broker')}  "
        f"complete: {report.get('total_complete_brackets')}  incomplete: {report.get('total_incomplete_brackets')}",
        f"- total notional: {report.get('total_notional')}  total_r: {report.get('total_r')}  avg_r: {report.get('avg_r')}",
        "",
        "## 2. Signal Frequency",
        f"- See day_breakdown in JSON; no_signal / cap days in recurring_blockers.",
        "",
        "## 3. Execution Quality",
        f"- Complete brackets: {report.get('total_complete_brackets')}  incomplete: {report.get('total_incomplete_brackets')}",
        "",
        "## 4. Strategy Performance",
        f"- total_r: {report.get('total_r')}  avg_r (from daily): {report.get('avg_r')}",
        "",
        "## 5. Ticker Ranking",
    ]
    def _es(v: Mapping[str, Any]) -> str:
        e = v.get("edge_score")
        if e is None:
            return "—"
        try:
            return f"{float(e):.2f}"
        except (TypeError, ValueError):
            return "—"

    rows = [
        f"| {sym} | {v.get('signals', 0)} | {v.get('orders', 0)} | {v.get('complete_brackets', 0)} | "
        f"{(v.get('avg_r') if v.get('avg_r') is not None else '—')} | "
        f"{_es(v)} |"
        for sym, v in list(tbd.items())[:30]
    ]
    lines += [
        "| symbol | signals | orders | complete | avg R | edge |",
        "|--------|---------|--------|----------|------|------|",
        *rows,
        "",
        "## 6. Main Blockers",
        f"- {rb}",
        "",
        "## 7. Safety Review",
        f"- {rec}",
        "",
        "## 8. Recommendations for Next Week",
        f"- keep: {rec.get('tickers_to_keep', [])}",
        f"- more data: {rec.get('tickers_need_more_data', [])[:12]}",
        f"- over_restricted: {rec.get('over_restricted_signal_flow')}  cap_blocking: {rec.get('sizing_caps_likely_blocking')}",
        f"- loop_ok: {rec.get('loop_safe_to_continue')}",
        "",
    ]
    return "\n".join(lines)


def format_paper_daily_telegram_zh(report: Mapping[str, Any]) -> str:
    """HTML-safe short digest; caller may use Telegram HTML parse_mode."""
    d = report.get("date", "?")
    ex = report.get("execution_summary") or {}
    sc = report.get("scan_summary") or {}
    bud = report.get("budget") or {}
    st = report.get("data_status", "")
    return (
        f"<b>纸面日报 {d}</b> ({st})\n"
        f"扫描 {sc.get('symbols_scanned', 0)} · STRICT/AGG {sc.get('strict_ready_count', 0)}/"
        f"{sc.get('aggressive_ready_count', 0)}\n"
        f"审计行 {ex.get('paper_orders_count', 0)} · 已挂经纪 "
        f"{ex.get('submitted_to_broker_count', 0)} · 完整括号 {ex.get('complete_bracket_count', 0)}\n"
        f"日度名义 {bud.get('today_submitted_notional_usd', 0)}/{bud.get('max_daily_notional_usd', 0)}"
    )