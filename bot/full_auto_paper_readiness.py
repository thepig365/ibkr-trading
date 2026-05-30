"""Full-auto paper engine readiness (read-only). Safe for UI — no IBKR unless probe_ibkr."""

from __future__ import annotations

import json
import socket
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from .automatic_paper_preflight import build_automatic_paper_engine_preflight
from .config import AppConfig
from .intraday_entry_window import (
    between_overnight_sessions_weekday_quiet,
    entry_timezone_now_display,
    intraday_entries_overnight,
    intraday_new_entries_allow_config,
    intraday_session_label,
    minute_of_day,
    ny_premarket_compatible,
    parse_hhmm,
)
from .journal import Journal
from .reports.news_monitor_readiness import build_news_monitor_readiness

NY = ZoneInfo("America/New_York")
FULL_AUTO_STATE_RELPATH = "data/runtime/full_auto_paper_supervisor_state.json"

_MORNING = (9 * 60 + 45, 11 * 60 + 30)
_RTH = (9 * 60 + 45, 15 * 60 + 30)
_PREMARKET_START = 8 * 60 + 30


class FullAutoStatus(StrEnum):
    READY_TO_RUN = "ready_to_run"
    WAITING_FOR_SESSION = "waiting_for_session"
    BLOCKED_USER_ACTION = "blocked_user_action_required"
    BLOCKED_SAFETY = "blocked_safety_gate"
    RUNNING = "running"
    DONE = "done"


def tws_port_listening(
    host: str,
    port: int,
    *,
    timeout_sec: float = 0.5,
) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_sec)
    try:
        s.connect((host, int(port)))
    except OSError:
        return False
    else:
        s.close()
        return True


def _ny_parts() -> tuple[datetime, int, int, str]:
    now = datetime.now(NY)
    w = int(now.weekday())
    m = now.hour * 60 + now.minute
    return now, w, m, now.strftime("%H:%M")


def in_trading_window_full(weekday: int, minutes: int) -> bool:
    """Legacy NY helper (09:45–15:30 Mon–Fri). Prefer :func:`intraday_new_entries_allow_config`."""

    if weekday >= 5:
        return False
    return _RTH[0] <= minutes <= _RTH[1]


def in_trading_window_morning(weekday: int, minutes: int) -> bool:
    if weekday >= 5:
        return False
    return _MORNING[0] <= minutes <= _MORNING[1]


def in_premarket_check_window(weekday: int, minutes: int) -> bool:
    if weekday >= 5:
        return False
    return _PREMARKET_START <= minutes < _RTH[0]


def _read_supervisor_state(root: Path) -> dict[str, Any]:
    p = root / FULL_AUTO_STATE_RELPATH
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _user_action_blocker(blockers: list[str], tws_ok: bool | None) -> bool:
    if tws_ok is False:
        return True
    for b in blockers:
        bl = (b or "").lower()
        if "ibkr probe" in bl:
            return True
        if "reconciliation" in bl and "broker" in bl:
            return True
        if "tws" in bl or "accepting tcp" in bl or "host/port" in bl:
            return True
    return False


def build_full_auto_paper_readiness(
    project_root: Path,
    cfg: AppConfig,
    journal: Journal | None,
    *,
    probe_ibkr: bool = False,
    session: str = "full",
    ui_safe: bool = False,
) -> dict[str, Any]:
    """If ``ui_safe`` is True, skip TCP/IBKR probes (Strategy Lab page render)."""
    root = Path(project_root).resolve()
    now_ny, nw, nm, ny_hhmm = _ny_parts()
    ip = cfg.settings.trading.intraday_paper
    entry_iso, entry_hhmm, ew, em = entry_timezone_now_display(ip)
    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        sess = "full"

    host = str(getattr(cfg.ibkr, "host", "127.0.0.1") or "127.0.0.1")
    try:
        port = int(getattr(cfg.ibkr, "port", 7497) or 7497)
    except (TypeError, ValueError):
        port = 7497

    if ui_safe:
        tws_ok: bool | None = None
        st_sup = _read_supervisor_state(root)
        v = st_sup.get("tws_listening_last")
        if isinstance(v, bool):
            tws_ok = v
    else:
        tws_ok = tws_port_listening(host, port)
    j = journal if probe_ibkr and not ui_safe else None
    pre = build_automatic_paper_engine_preflight(
        cfg, j, probe_ibkr=bool(probe_ibkr and journal and not ui_safe)
    )

    blockers: list[str] = list(pre.get("blockers", []) or [])
    if not ui_safe and not tws_ok:
        blockers.insert(0, "TWS or IB Gateway not accepting TCP on configured host/port")
    elif (
        ui_safe
        and tws_ok is False
        and "TWS or IB Gateway" not in str(blockers)
    ):
        blockers.insert(0, "TWS or IB Gateway not accepting TCP (last supervisor check)")

    tws_listening_out: bool | None
    if ui_safe:
        tws_listening_out = tws_ok
    else:
        tws_listening_out = bool(tws_ok)

    if sess == "morning":
        in_win = in_trading_window_morning(nw, nm)
        weekend = nw >= 5
        in_pre = in_premarket_check_window(nw, nm)
        win_label = "09:45–11:30 NY"
        weekday_for_ok = nw < 5
        post_entry_quiet = nw < 5 and nm > _MORNING[1]
    else:
        in_win, _ = intraday_new_entries_allow_config(ip)
        weekend = ew >= 5
        in_pre = ny_premarket_compatible(ip, ny_weekday=nw, ny_minute=nm)
        win_label = intraday_session_label(ip)
        weekday_for_ok = ew < 5
        sh = minute_of_day(*parse_hhmm(ip.no_new_entries_before))
        eh = minute_of_day(*parse_hhmm(ip.no_new_entries_after))
        if intraday_entries_overnight(open_m=sh, close_m=eh):
            post_entry_quiet = between_overnight_sessions_weekday_quiet(
                ip, weekday=ew, minute=em
            )
        else:
            post_entry_quiet = ew < 5 and em > eh

    sup = _read_supervisor_state(root)
    engine_flag = bool(sup.get("engine_running")) or str(
        sup.get("supervisor_phase", "")
    ).lower() in {"engine", "running"}

    nmon = build_news_monitor_readiness(root, cfg)
    nprov = int(nmon.get("providers_count") or 0)

    if not ui_safe:
        tws_for_ok = bool(tws_ok)
    elif tws_ok is None:
        tws_for_ok = False
    else:
        tws_for_ok = bool(tws_ok)
    ok = bool(pre.get("ok")) and tws_for_ok and in_win and weekday_for_ok

    if engine_flag:
        status = FullAutoStatus.RUNNING.value
    elif weekend:
        status = FullAutoStatus.WAITING_FOR_SESSION.value
    elif post_entry_quiet:
        status = FullAutoStatus.DONE.value
    elif not in_win:
        status = FullAutoStatus.WAITING_FOR_SESSION.value
    elif ok:
        status = FullAutoStatus.READY_TO_RUN.value
    elif _user_action_blocker(blockers, tws_ok):
        status = FullAutoStatus.BLOCKED_USER_ACTION.value
    else:
        status = FullAutoStatus.BLOCKED_SAFETY.value

    next_action = "none"
    if status == FullAutoStatus.RUNNING.value:
        next_action = "supervisor_engine_active"
    elif not in_win and not weekend:
        next_action = "wait_for_trading_window"
    elif in_pre:
        next_action = "premarket_readiness_checks"
    elif ok:
        next_action = "start_full_auto_supervisor_or_engine"
    elif blockers:
        next_action = "resolve_blockers_then_retry"
    else:
        next_action = "review_status"

    reconcile = str(pre.get("reconciliation_loop_state") or "")
    if probe_ibkr and pre.get("reconciliation_passed_probe") is not None:
        reconcile = "passed" if pre.get("reconciliation_passed_probe") else "failed"

    return {
        "ok": ok,
        "status": status,
        "blockers": blockers,
        "current_ny_time": now_ny.isoformat(),
        "current_ny_hhmm": ny_hhmm,
        "intraday_entry_wall_clock_tz": ip.new_entries_wall_clock_timezone,
        "intraday_entry_local_time": entry_iso,
        "intraday_entry_local_hhmm": entry_hhmm,
        "session": sess,
        "session_window": win_label,
        "in_trading_window": in_win,
        "premarket_check_window": in_pre,
        "tws_listening": tws_listening_out,
        "ibkr_connected": bool(pre.get("reconciliation_passed_probe"))
        if probe_ibkr and not ui_safe
        else None,
        "paper_account": str(getattr(cfg.settings.account, "mode", "")).lower() == "paper",
        "active_strategy": str(pre.get("active_paper_strategy", "")),
        "daily_remaining_notional_usd": pre.get("daily_remaining_notional_usd"),
        "kill_switch": (root / "data" / "KILL_SWITCH").is_file(),
        "reconcile": reconcile or "unknown",
        "telegram_configured": bool(pre.get("telegram_configured"))
        or bool(nmon.get("telegram_configured")),
        "news_providers_count": nprov,
        "next_action": next_action,
        "preflight": pre,
    }
