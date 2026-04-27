"""Read-only text bodies for Telegram /status, /news, /reports (no orders)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .config import AppConfig
from .full_auto_paper_readiness import build_full_auto_paper_readiness
from .journal import Journal
from .launchd_full_auto_ui import user_launchd_plist_path
from .reports.news_monitor_readiness import build_news_monitor_readiness
from .reports.report_hub_ui import _compact_paper_daily, _compact_weekly, _edge_top_rows
from .reports.report_paths import (
    DEFAULT_REPORT_DIR,
    EDGE_DIR,
    backtest_summary_latest,
    default_report_dir,
    latest_glob_path,
    safe_read_json,
)
from .reports.telegram_report_dedup import read_state as read_tg_dedup_state

FULL_AUTO_LABEL = "com.strategy-lab.full-auto-paper"
LISTENER_PLIST = "com.strategy-lab.telegram-listener"


def _plist_installed(label: str) -> bool:
    p = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    return p.is_file()


def _launchctl_list() -> str:
    try:
        r = subprocess.run(
            ["/bin/launchctl", "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"launchctl: {exc!s}"
    if r.returncode != 0:
        return f"launchctl: fail {((r.stderr or r.stdout) or '')[:200]!s}"
    return r.stdout or ""


def launchctl_line_for_label(label: str) -> str:
    for line in _launchctl_list().splitlines():
        if label in line:
            return line.strip()[:200]
    return f"(not listed: {label})"


def format_status_telegram_zh(cfg: AppConfig, journal: Journal) -> str:
    """Engine-style /status: readiness + launchd. TCP probe for TWS; no IBKR RPC."""
    root = cfg.project_root
    r = build_full_auto_paper_readiness(
        root, cfg, journal, probe_ibkr=False, session="full", ui_safe=False
    )
    pl_full = _plist_installed(FULL_AUTO_LABEL)
    pl_listen = _plist_installed(LISTENER_PLIST)
    pre = (r.get("preflight") or {}) if isinstance(r, dict) else {}
    p_ok = pre.get("ok")
    return "\n".join(
        [
            "【/status 引擎摘要】",
            f"- 当前 NY: {r.get('current_ny_hhmm', '—')}  ({r.get('current_ny_time', '—')})",
            f"- 交易窗 ({r.get('session', 'full')}): {r.get('session_window', '—')}",
            f"- readiness: {r.get('status', '—')}",
            f"- 整体 ok(窗口+门控): {r.get('ok', False)}",
            f"- TWS 端口监听: {r.get('tws_listening', '—')}",
            f"- 纸面账户: {r.get('paper_account', '—')}",
            f"- 当前策略: {r.get('active_strategy', '—')}",
            f"- 日剩余预算 (USD 约): {r.get('daily_remaining_notional_usd', '—')}",
            f"- 杀机 KILL 文件: {r.get('kill_switch', '—')}",
            f"- 对账: {r.get('reconcile', '—')}",
            f"- next_action: {r.get('next_action', '—')}",
            f"- 阻塞: {r.get('blockers') or []}",
            f"- preflight.ok: {p_ok}  preflight 阻塞: {pre.get('blockers') or []}",
            "",
            "【launchd】",
            f"- full-auto-paper plist: {'yes' if pl_full else 'no'}  ({user_launchd_plist_path()})",
            f"- 任务: {launchctl_line_for_label(FULL_AUTO_LABEL)[:180]}",
            f"- telegram-listener plist: {'yes' if pl_listen else 'no'}",
            f"- 任务: {launchctl_line_for_label(LISTENER_PLIST)[:180]}",
            "",
            "完整请打开 Web UI: /reports（Reports 页）。",
        ]
    )


def format_news_dry_telegram_zh(cfg: AppConfig) -> str:
    """/news: provider + last check — no new API calls."""
    root = Path(cfg.project_root)
    nmon = build_news_monitor_readiness(root, cfg)
    nr = cfg.settings.news_reporting
    st_path = root / nr.state_relpath
    st: dict[str, Any] = read_tg_dedup_state(st_path) if st_path.is_file() else {}
    last = (st.get("last_result") or {}) if isinstance(st, dict) else {}
    return "\n".join(
        [
            "【/news 监控状态（不主动拉取 API）】",
            f"- news_reporting.enabled: {nmon.get('news_reporting.enabled', '—')}",
            f"- 已配置 key 的提供商数: {nmon.get('providers_count', 0)}",
            f"- send_no_news_messages: {nmon.get('send_no_news_messages', '—')}",
            f"- last_items_scored: {last.get('items_scored', '—')}",
            f"- last telegram_status: {last.get('telegram_status', '—')}",
            f"- state: {nmon.get('state_path', '—')}",
            f"- blocking_reasons: {nmon.get('blocking_reasons') or []}",
            "完整见 UI /reports 或本机 `market-news-check`。",
        ]
    )


def format_reports_telegram_zh(cfg: AppConfig) -> str:
    """Paths for latest report artifacts (read files only)."""
    root = Path(cfg.project_root)
    rdir = default_report_dir(root)
    daily = latest_glob_path(rdir, "*-paper-daily-report.json")
    daily_md = latest_glob_path(rdir, "*-paper-daily-report.md")
    wk = latest_glob_path(rdir, "*-paper-weekly-report.json")
    bt = backtest_summary_latest(root)
    edge = latest_glob_path(root / EDGE_DIR, "*-edge-profiles.json")
    lines: list[str] = ["【/reports 最新产物】", f"- 报告目录: {DEFAULT_REPORT_DIR}"]
    if daily:
        dj = safe_read_json(daily) or {}
        comp = _compact_paper_daily(dj)
        lines.append(
            f"- 纸面日报: {daily.name}  date={comp.get('date', '—')}"
        )
        if daily_md:
            lines.append(f"  → MD: {daily_md.name}")
    else:
        lines.append("- 纸面日报: 无")
    if wk:
        wj = safe_read_json(wk) or {}
        compw = _compact_weekly(wj)
        lines.append(f"- 周报: {wk.name}  {compw.get('week_start', '—')}")
    if bt:
        bj = safe_read_json(bt) or {}
        m = bj.get("metrics") or {}
        ntr = m.get("total_trades", "—")
        lines.append(f"- 回测摘要: {bt.relative_to(root)}  total_trades={ntr}")
    else:
        lines.append("- 回测摘要: 无")
    if edge:
        ej = safe_read_json(edge) or {}
        top = _edge_top_rows(ej, limit=3)
        syms = ", ".join(
            str(x.get("symbol") or "")
            for x in top
            if isinstance(x, dict)
        ) or "—"
        lines.append(f"- edge: {edge.relative_to(root)}  top: {syms}")
    else:
        lines.append("- edge: 无")
    lines.append("浏览器打开 /reports 。")
    return "\n".join(lines)


__all__ = [
    "format_news_dry_telegram_zh",
    "format_reports_telegram_zh",
    "format_status_telegram_zh",
    "launchctl_line_for_label",
    "LISTENER_PLIST",
]
