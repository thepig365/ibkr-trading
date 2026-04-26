"""Read-only auto paper intraday loop readiness (13L.1-PREP). No loop, no orders."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ny_session_windows import us_morning_paper_window_allows
from .config import AppConfig
from .execution.intraday_paper_execution import (
    INTRADAY_AUTO_PAPER_ENABLED_RELPATH,
    INTRADAY_LOOP_STATE_RELPATH,
    KILL_SWITCH_RELPATH,
    is_intraday_paper_runtime_enabled,
    is_kill_switch_active,
)
from .execution.intraday_paper_sizing import ledger_snapshot_for_status
from .journal import Journal
from .paper_activation import build_paper_activation_status
from .reports.report_email_status import load_report_email_status
from .strategy_ui import load_strategy_selection, load_strategy_ui_catalog

# Reference times for UI/docs (US cash, America/New_York). RTH gating in code uses
# no_new_entries before/after from config (default 09:45–15:30 NY).
_NY_RTH_DISPLAY = {
    "timezone": "America/New_York",
    "market_open": "09:30",
    "no_new_entries_before_config": None,  # filled from config
    "no_new_entries_after_config": None,
    "exit_open_positions_at_config": None,
    "rth_close_reference": "16:00",
    "daily_report_window_reference": "16:05–16:30 (manual or scheduler; not auto-started from loop)",
}

def _read_json(p: Path) -> dict[str, Any] | None:
    if not p.is_file():
        return None
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return o if isinstance(o, dict) else None


def _latest_glob(dir_path: Path, pattern: str) -> Path | None:
    if not dir_path.is_dir():
        return None
    files = sorted(dir_path.glob(pattern))
    return files[-1] if files else None


def _scan_edge_hints(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    scan_dir = root / "data" / "intraday_smc"
    edge_dir = root / "data" / "edge_profiles"
    latest_scan = _latest_glob(scan_dir, "*-watchlist-intraday-smc-summary.json")
    latest_edge = _latest_glob(edge_dir, "*-edge-profiles.json")
    out: dict[str, Any] = {
        "latest_scan_path": str(latest_scan) if latest_scan else None,
        "latest_edge_path": str(latest_edge) if latest_edge else None,
    }
    if latest_scan is not None:
        j = _read_json(latest_scan)
        if j:
            out["latest_scan_date"] = (j.get("date") or j.get("summary") or "") or latest_scan.stem[:10]
    if latest_edge is not None:
        j = _read_json(latest_edge)
        if j:
            out["latest_edge_profile_date"] = j.get("profile_date") or j.get("date")
    return out


def _pick_next_action(
    *,
    kill: bool,
    paper_ok: bool,
    config_safe: bool,
    rem: float | None,
    loop_recon: str,
    require_recon: bool,
    final_ready: str,
) -> str:
    if kill:
        return "kill_switch_active"
    if not config_safe:
        return "unsafe_config"
    if not paper_ok:
        return "paper_strategy_not_enabled"
    if rem is not None and rem <= 0:
        return "wait_for_daily_budget"
    rs = (loop_recon or "").lower()
    if require_recon and ("fail" in rs or "error" in rs):
        return "fix_reconcile"
    if final_ready != "READY_FOR_PAPER_TEST":
        return "run_readiness_check"
    return "ready_for_60min_smoke"


def _today_ny_ymd() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _ny_weekday_minutes() -> tuple[int, int, int]:
    """(weekday 0-6, minutes since midnight NY, full minutes 0-1439 for sorting)."""
    z = ZoneInfo("America/New_York")
    now = datetime.now(z)
    w = int(now.weekday())
    m = now.hour * 60 + now.minute
    return w, m, m


def _compute_morning_next_safe_action(
    *,
    kill: bool,
    ict_paper: bool,
    config_safe: bool,
    rem: float | None,
    loop_recon: str,
    require_recon: bool,
    final_ready: str,
    latest_scan_path: str | None,
    latest_scan_date: str | None,
    probe_ibkr: bool,
    pa: dict[str, Any],
) -> str:
    """Action codes for a future ``--session morning`` smoke (does not start the loop)."""
    if kill:
        return "kill_switch_active"
    if not ict_paper:
        return "paper_strategy_not_enabled"
    if not config_safe:
        return "not_ready"
    if rem is not None and rem <= 0:
        return "wait_for_daily_budget"
    rs = (loop_recon or "").lower()
    if require_recon and ("fail" in rs or "error" in rs):
        return "reconcile_not_passed"

    w, minutes, _ = _ny_weekday_minutes()
    if w >= 5:
        return "wait_for_market_open"
    # Weekday: no entries before 09:45 NY (stabilization after 09:30 open)
    if minutes < 9 * 60 + 45:
        return "wait_for_market_open"
    in_morning, _ = us_morning_paper_window_allows()
    if not in_morning:
        # e.g. after 11:30 NY, or not a US session day for cash equities
        return "not_ready"

    if final_ready != "READY_FOR_PAPER_TEST":
        return "not_ready"

    if probe_ibkr and pa.get("ibkr_probe_error"):
        return "tws_not_connected"
    if probe_ibkr and pa.get("reconciliation_passed") is False:
        return "tws_not_connected"

    scan_ok = bool(latest_scan_path)
    if not scan_ok:
        return "no_recent_scan"
    today = _today_ny_ymd()
    if latest_scan_date and str(latest_scan_date).strip()[:10] != today:
        return "no_recent_scan"

    return "ready_for_morning_smoke"


def _morning_reason(code: str, *, recon: str) -> str:
    mapping = {
        "ready_for_morning_smoke": "Within 09:45–11:30 NY; ICT paper path, budget, and activation checks pass (loop not started by this command)",
        "wait_for_market_open": "Wait for US session entry window (weekday, from 09:45 NY) or next session",
        "wait_for_daily_budget": "Daily paper notional cap reached for today (UTC ledger)",
        "tws_not_connected": "TWS/IB paper probe failed or reconciliation did not pass (when --probe-ibkr)",
        "paper_strategy_not_enabled": "Active paper strategy is not ICT/SMC with paper enabled",
        "kill_switch_active": "data/KILL_SWITCH is present",
        "reconcile_not_passed": f"Reconciliation required but last status hints failure: {recon!r}",
        "no_recent_scan": "No intraday scan summary for today (data/intraday_smc) or missing file",
        "not_ready": "Config/activation/morning window conditions not all satisfied",
    }
    return mapping.get(code, code)


def build_auto_loop_readiness(
    project_root: Path | str,
    cfg: AppConfig,
    journal: Journal | None,
    *,
    probe_ibkr: bool = False,
) -> dict[str, Any]:
    """Aggregate read-only status for a future auto loop test. Never starts the loop."""
    root = Path(project_root).resolve()
    ip = cfg.settings.trading.intraday_paper

    cat = load_strategy_ui_catalog(root)
    sel = load_strategy_selection(root, catalog=cat)
    paper_entry = cat.strategies.get(sel.active_paper_strategy)
    paper_enabled = bool(paper_entry and paper_entry.paper_enabled)
    ict_paper = sel.active_paper_strategy == "ict_smc_intraday_v1" and paper_enabled

    kill = is_kill_switch_active(cfg)
    kill_path = Path(cfg.absolute(KILL_SWITCH_RELPATH))

    runtime_path = Path(cfg.absolute(INTRADAY_AUTO_PAPER_ENABLED_RELPATH))
    runtime_on = False
    if runtime_path.is_file():
        t = runtime_path.read_text(encoding="utf-8").strip().lower()
        runtime_on = t in ("1", "true", "yes", "on")

    eff_on, _exp = is_intraday_paper_runtime_enabled(cfg)

    loop_st = _read_json(Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH))) or {}
    recon_loop = str(loop_st.get("reconciliation_status") or "")

    ledger: dict[str, Any] = {}
    try:
        ledger = ledger_snapshot_for_status(cfg, ip)
    except (OSError, TypeError, ValueError):
        ledger = {}
    rem: float | None = None
    try:
        rem = float(ledger.get("daily_remaining_notional_usd", 0))
    except (TypeError, ValueError):
        rem = None

    config_safe = (
        ip.paper_only is True
        and ip.live_trading_allowed is False
        and ip.market_orders_allowed is False
        and bool(ip.bracket_required and ip.stop_required and ip.target_required)
    )

    pa = build_paper_activation_status(cfg, probe_ibkr=probe_ibkr, journal=journal)
    final_ready = str(pa.get("final_readiness") or "NOT_READY")

    next_act = _pick_next_action(
        kill=kill,
        paper_ok=ict_paper,
        config_safe=config_safe,
        rem=rem,
        loop_recon=recon_loop,
        require_recon=bool(ip.require_reconciliation_pass),
        final_ready=final_ready,
    )

    ny = {**_NY_RTH_DISPLAY}
    ny["no_new_entries_before_config"] = ip.no_new_entries_before
    ny["no_new_entries_after_config"] = ip.no_new_entries_after
    ny["exit_open_positions_at_config"] = ip.exit_open_positions_at

    resend = bool((os.environ.get("RESEND_API_KEY") or "").strip())
    rfrom = (os.environ.get("REPORT_EMAIL_FROM") or "").strip()
    report_email = load_report_email_status(
        root, resend_key_present=resend, from_addr=rfrom
    )
    re_status = "configured" if resend and rfrom else "skipped_missing_credentials"

    hints = _scan_edge_hints(root)
    lspath = hints.get("latest_scan_path")
    lsdate = hints.get("latest_scan_date")
    if lspath and not lsdate:
        sp = Path(str(lspath))
        if len(sp.stem) >= 10 and sp.stem[4] == "-" and sp.stem[7] == "-":
            lsdate = sp.stem[:10]

    morning_next = _compute_morning_next_safe_action(
        kill=kill,
        ict_paper=ict_paper,
        config_safe=config_safe,
        rem=rem,
        loop_recon=recon_loop,
        require_recon=bool(ip.require_reconciliation_pass),
        final_ready=final_ready,
        latest_scan_path=str(lspath) if lspath else None,
        latest_scan_date=str(lsdate) if lsdate else None,
        probe_ibkr=probe_ibkr,
        pa=pa,
    )

    tws: dict[str, Any] = {"probed": bool(probe_ibkr)}
    if probe_ibkr and journal is not None and pa.get("reconciliation") is not None:
        tws["reconciliation"] = pa.get("reconciliation")
    if probe_ibkr and journal is not None and pa.get("ibkr_probe_error"):
        tws["error"] = pa.get("ibkr_probe_error")
    if probe_ibkr and journal is not None and "reconciliation_passed" in pa:
        tws["reconciliation_passed"] = pa.get("reconciliation_passed")

    out: dict[str, Any] = {
        "active_paper_strategy": sel.active_paper_strategy,
        "paper_enabled": paper_enabled,
        "selected_strategy_paper_enabled": paper_enabled,
        "ict_paper_path_ok": ict_paper,
        "intraday_runtime_on": bool(eff_on),
        "runtime_flag_path": str(runtime_path),
        "runtime_file_raw": runtime_path.read_text(encoding="utf-8").strip()
        if runtime_path.is_file()
        else None,
        "kill_switch": kill,
        "kill_switch_path": str(kill_path),
        "reconcile_last_status": recon_loop or None,
        "require_reconciliation_pass": bool(ip.require_reconciliation_pass),
        "paper_activation_final_readiness": final_ready,
        "daily_remaining_notional_usd": rem,
        "max_notional_per_order_usd": float(ip.max_notional_per_order_usd),
        "max_daily_notional_usd": float(ip.max_daily_notional_usd),
        "market_orders_allowed": bool(ip.market_orders_allowed),
        "live_trading_allowed": bool(ip.live_trading_allowed),
        "bracket_required": bool(ip.bracket_required),
        "stop_required": bool(ip.stop_required),
        "target_required": bool(ip.target_required),
        "intraday_paper_config_enabled": bool(ip.enabled),
        "dry_run": bool(ip.dry_run),
        "config_invariants_ok": config_safe,
        "new_york_session_reference": ny,
        "loop_state_path": str(Path(cfg.absolute(INTRADAY_LOOP_STATE_RELPATH))),
        "next_safe_action": next_act,
        "readiness": "Ready" if next_act == "ready_for_60min_smoke" else "Not ready",
        "readiness_reason": _reason_for(
            next_act, kill, rem, ict_paper, config_safe, recon_loop, final_ready
        ),
        "report_email_status": re_status,
        "report_email_detail": asdict(report_email),
        "latest_scan_and_edge": hints,
        "tws": tws,
        "morning_session_supported": True,
        "morning_window_start_ny": "09:45",
        "morning_window_end_ny": "11:30",
        "no_new_entries_before_ny": "09:45",
        "morning_next_safe_action": morning_next,
        "morning_readiness": (
            "Ready" if morning_next == "ready_for_morning_smoke" else "Not ready"
        ),
        "morning_readiness_reason": _morning_reason(morning_next, recon=recon_loop),
        "commands": {
            "start_loop": "python3 -m bot.cli run-auto-paper-intraday-loop (Ctrl+C to stop; not run by this check)",
            "start_morning_loop": "python3 -m bot.cli run-auto-paper-intraday-loop --session morning (09:45–11:30 NY; planned smoke; not from UI)",
            "stop_loop": "Ctrl+C in terminal, or set kill switch / intraday runtime OFF as documented",
        },
    }
    if probe_ibkr:
        out["paper_activation_probe"] = {k: v for k, v in pa.items() if k in (
            "reconciliation_passed", "reconciliation", "ibkr_probe_error", "probe_ibkr"
        )}
    return out


def _reason_for(
    next_act: str,
    kill: bool,
    rem: float | None,
    ict: bool,
    config_safe: bool,
    recon: str,
    final_ready: str,
) -> str:
    if kill:
        return "data/KILL_SWITCH is present"
    if not config_safe:
        return "Intraday paper config invariants (paper-only, no live, no market, bracket/stop/target) not satisfied"
    if not ict:
        return "Active paper strategy is not ICT/SMC (paper_enabled) in strategy_ui selection"
    if rem is not None and rem <= 0:
        return "Daily paper notional budget appears exhausted for today (UTC ledger)"
    rs = (recon or "").lower()
    if "fail" in rs or "error" in rs:
        return f"Last loop reconciliation hint: {recon}"
    if final_ready != "READY_FOR_PAPER_TEST":
        return "Paper activation is not READY_FOR_PAPER_TEST (local config/runtime)"
    if next_act == "ready_for_60min_smoke":
        return "Core file checks pass; you may plan a time-bounded loop test (this command does not start it)"
    return next_act


__all__ = ["build_auto_loop_readiness"]
