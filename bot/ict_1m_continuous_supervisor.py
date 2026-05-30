"""ICT 1m continuous paper supervisor — Forex + US stock sessions on one long-lived loop.

* Outside strategy windows: no new trade submission paths; TWS health + state file still update.
* Kill switch: blocks new entries; loop keeps running until SIGINT/SIGTERM or process exit.
* Does not modify ``trading.enabled``, ``intraday_paper.dry_run``, or Forex ``submit_to_broker``.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from bot.config import AppConfig, load_config
from bot.execution.intraday_paper_execution import (
    is_kill_switch_active,
    run_intraday_paper_pass,
)
from bot.forex.auto_paper_supervisor import run_forex_auto_paper_supervisor
from bot.forex.config import load_forex_ict_config
from bot.intraday_entry_window import parse_hhmm, tz_weekday_new_entries_allow
from bot.journal import Journal
from bot.tws_health_alerts import check_tws_health_for_alerts, maybe_send_tws_health_alert

LOG = logging.getLogger(__name__)

STATE_RELPATH = "data/runtime/ict_1m_continuous_supervisor.json"
CONFIG_RELPATH = "config/ict_1m_continuous.yaml"


def state_path(root: Path) -> Path:
    return root.resolve() / STATE_RELPATH


def load_ict_1m_continuous_config(project_root: Path) -> dict[str, Any]:
    p = project_root.resolve() / CONFIG_RELPATH
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def melbourne_window_allowed(
    tz_name: str,
    start_hhmm: str,
    end_hhmm: str,
    *,
    now_utc: datetime | None = None,
) -> tuple[bool, str]:
    sh = parse_hhmm(start_hhmm)
    eh = parse_hhmm(end_hhmm)
    zn = (tz_name or "Australia/Melbourne").strip()
    if now_utc is None:
        now_local = None
    else:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(zn)
        now_local = now_utc.astimezone(z)
    return tz_weekday_new_entries_allow(
        zn, start_hhmm=sh, end_hhmm=eh, now_local=now_local
    )


@dataclass
class _LoopCfg:
    interval: float
    fx_tz: str
    fx_start: str
    fx_end: str
    us_tz: str
    us_start: str
    us_end: str


def _parse_loop_cfg(project_root: Path) -> _LoopCfg:
    raw = load_ict_1m_continuous_config(project_root)
    interval = float(raw.get("loop_interval_seconds") or 60)
    interval = max(5.0, interval)
    fx = raw.get("forex") if isinstance(raw.get("forex"), dict) else {}
    us = raw.get("us_stock") if isinstance(raw.get("us_stock"), dict) else {}
    return _LoopCfg(
        interval=interval,
        fx_tz=str(fx.get("timezone") or "Australia/Melbourne"),
        fx_start=str(fx.get("session_start") or "08:00"),
        fx_end=str(fx.get("session_end") or "22:00"),
        us_tz=str(us.get("timezone") or "Australia/Melbourne"),
        us_start=str(us.get("session_start") or "22:30"),
        us_end=str(us.get("session_end") or "01:00"),
    )


def build_ict_1m_continuous_ui_context(
    project_root: Path | str, *, cfg: AppConfig | None = None
) -> dict[str, Any]:
    """Dashboard: read last written supervisor state (no IBKR in render path)."""

    root = Path(project_root).resolve()
    p = state_path(root)
    if not p.is_file():
        return {
            "state_present": False,
            "hint": "Start: python3 -m bot.cli run-ict-1m-continuous-loop",
        }
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"state_present": False, "hint": "state file unreadable"}
    if not isinstance(st, dict):
        return {"state_present": False}
    cfg = cfg or load_config(project_root=root)
    lc = _parse_loop_cfg(root)
    return {
        "state_present": True,
        "loop_config": {
            "interval_seconds": lc.interval,
            "forex_window": f"{lc.fx_start}–{lc.fx_end} {lc.fx_tz}",
            "us_stock_window": f"{lc.us_start}–{lc.us_end} {lc.us_tz}",
        },
        **st,
        "hint": None,
        "intraday_runtime_note": (
            "US path needs intraday runtime ON + trading.intraday_paper.enabled; "
            "see paper page / intraday-paper-on"
        ),
    }


def _write_state(root: Path, payload: dict[str, Any]) -> None:
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _next_check_iso(interval_sec: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=interval_sec)).isoformat()


def _forex_submit_flags(root: Path) -> tuple[bool, bool]:
    fx = load_forex_ict_config(root)
    ap = fx.get("auto_paper") if isinstance(fx.get("auto_paper"), dict) else {}
    ex = fx.get("execution") if isinstance(fx.get("execution"), dict) else {}
    return bool(ap.get("enabled", False)), bool(ex.get("submit_to_broker", False))


def _derive_block_reason(
    *,
    kill: bool,
    fx_open: bool,
    us_open: bool,
    tws_healthy: bool,
    trading_enabled: bool,
    ip_enabled: bool,
    ip_dry_run: bool,
    forex_dry_run: bool,
    auto_on: bool,
    sub_broker: bool,
    last_forex_blockers: list[str] | None,
    last_us_reason: str,
    last_orders_fx: int,
    last_orders_us: int,
) -> str:
    if kill:
        return "kill_switch"
    if not fx_open and not us_open:
        return "outside_session"
    if not tws_healthy:
        return "broker_not_ready"
    if fx_open:
        if forex_dry_run:
            return "dry_run"
        if not auto_on or not sub_broker:
            return "risk_block"
        if last_forex_blockers:
            blockers_low = ",".join(x.lower() for x in last_forex_blockers[:12])
            if "no_tradeable_signal" in blockers_low:
                return "no_signal"
            if "cap_block" in blockers_low or "max_daily" in blockers_low:
                return "max_trades"
            if "max_trade" in blockers_low or "max_trades" in blockers_low:
                return "max_trades"
            return "risk_block"
        if last_orders_fx <= 0:
            return "no_signal"
        return "none"
    if us_open:
        if not trading_enabled or not ip_enabled:
            return "risk_block"
        if ip_dry_run:
            return "dry_run"
        low = (last_us_reason or "").lower()
        if "reconcil" in low and "fail" in low:
            return "broker_not_ready"
        if "notional" in low and ("cap" in low or "daily" in low):
            return "max_trades"
        if last_orders_us <= 0:
            return "no_signal"
        return "none"
    return "outside_session"


def run_ict_1m_continuous_loop(
    project_root: Path | str,
    *,
    cfg: AppConfig | None = None,
    journal: Journal | None = None,
    interval_override: float | None = None,
    once: bool = False,
    max_iterations: int | None = None,
    forex_dry_run: bool = False,
    us_telegram: bool = False,
    sleep_fn: Callable[[float], None] | None = None,
    run_tws_health_telegram: bool = True,
) -> dict[str, Any]:
    """Long-lived loop until SIGINT/SIGTERM (or ``once=True`` for a single tick)."""

    root = Path(project_root).resolve()
    cfg = cfg or load_config(project_root=root)
    journal = journal or Journal(cfg)
    lc = _parse_loop_cfg(root)
    interval = float(interval_override) if interval_override is not None else lc.interval
    interval = max(5.0, interval)
    sl = sleep_fn or time.sleep

    stop = False

    def _stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    iterations = 0
    last_summary: dict[str, Any] = {}

    while not stop:
        iterations += 1
        now_utc = datetime.now(timezone.utc)
        auto_on, sub_broker = _forex_submit_flags(root)
        ip = cfg.settings.trading.intraday_paper

        fx_ok, fx_why = melbourne_window_allowed(
            lc.fx_tz, lc.fx_start, lc.fx_end, now_utc=now_utc
        )
        us_ok, us_why = melbourne_window_allowed(
            lc.us_tz, lc.us_start, lc.us_end, now_utc=now_utc
        )

        kill = is_kill_switch_active(cfg)
        ict_active = bool(fx_ok or us_ok)

        tws_st = check_tws_health_for_alerts(cfg, journal)
        tws_healthy = tws_st.status == "healthy"
        if run_tws_health_telegram:
            try:
                maybe_send_tws_health_alert(
                    cfg,
                    journal,
                    tws_st,
                    source="ict-1m-continuous-loop",
                    send_telegram=True,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.debug("tws health telegram path: %s", exc)

        last_fx: dict[str, Any] = {}
        last_us: dict[str, Any] = {}
        last_forex_blockers: list[str] | None = None
        last_us_reason = ""
        last_orders_fx = 0
        last_orders_us = 0
        monitor_note = ""

        try:
            if kill:
                monitor_note = "kill_switch — trading paths skipped"
            elif not fx_ok and not us_ok:
                monitor_note = f"outside_session fx={fx_why[:120]} us={us_why[:120]}"

            if not kill and fx_ok:
                LOG.info("ict-1m tick: forex supervisor")
                last_fx = run_forex_auto_paper_supervisor(
                    root, dry_run=forex_dry_run, cfg=cfg, journal=journal
                )
                last_forex_blockers = list(last_fx.get("blockers") or [])
                br_fx = last_fx.get("broker_result")
                if (
                    isinstance(br_fx, dict)
                    and br_fx.get("ok")
                    and last_fx.get("next_action") == "submitted_attempt"
                ):
                    last_orders_fx = 1
                else:
                    last_orders_fx = 0

            if not kill and us_ok:
                LOG.info("ict-1m tick: us intraday pass")
                us_res = run_intraday_paper_pass(
                    cfg,
                    journal,
                    source="dynamic",
                    limit=20,
                    telegram=us_telegram,
                    chart=False,
                )
                last_us_reason = str(us_res.last_reason or "")
                last_orders_us = int(us_res.orders_submitted)
                last_us = {
                    "last_status": us_res.last_status,
                    "last_reason": last_us_reason,
                    "orders_submitted": last_orders_us,
                    "skipped_reasons": list(us_res.skipped_reasons or [])[:12],
                }
        except Exception as exc:  # noqa: BLE001
            LOG.exception("ict-1m iteration failed")
            monitor_note = f"loop_error: {type(exc).__name__}: {exc!s}"[:500]

        br = _derive_block_reason(
            kill=kill,
            fx_open=bool(fx_ok),
            us_open=bool(us_ok),
            tws_healthy=tws_healthy,
            trading_enabled=bool(cfg.settings.trading.enabled),
            ip_enabled=bool(ip.enabled),
            ip_dry_run=bool(ip.dry_run),
            forex_dry_run=bool(forex_dry_run),
            auto_on=auto_on,
            sub_broker=sub_broker,
            last_forex_blockers=last_forex_blockers,
            last_us_reason=last_us_reason,
            last_orders_fx=last_orders_fx,
            last_orders_us=last_orders_us,
        )
        permitted = (
            not kill
            and ict_active
            and tws_healthy
            and br in {"none", "no_signal"}
        )
        fx_na = (
            last_fx.get("next_action") if isinstance(last_fx, dict) and last_fx else None
        )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "bot_running": True,
            "bot_runtime": "RUNNING",
            "iteration": iterations,
            "updated_utc": now_utc.isoformat(),
            "next_check_utc": _next_check_iso(interval),
            "ict_1m_time_active": ict_active,
            "ict_1m_status": ("active" if ict_active else "inactive"),
            "forex_session_active": bool(fx_ok),
            "us_stock_session_active": bool(us_ok),
            "kill_switch_active": kill,
            "forex_yaml_auto_paper_enabled": auto_on,
            "forex_yaml_submit_to_broker": sub_broker,
            "global_trading_enabled": bool(cfg.settings.trading.enabled),
            "intraday_paper_enabled": bool(ip.enabled),
            "intraday_paper_dry_run": bool(ip.dry_run),
            "supervisor_forex_dry_run_flag": bool(forex_dry_run),
            "tws_health_status": tws_st.status,
            "tws_alert_code": tws_st.alert_code,
            "session_labels": {
                "forex": f"{lc.fx_start}–{lc.fx_end} Melbourne",
                "us_stock": f"{lc.us_start}–{lc.us_end} Melbourne (overnight)",
            },
            "trading_permission": "ALLOWED" if permitted else "BLOCKED",
            "block_reason": br if not permitted else "none",
            "window_detail": {"forex": fx_why, "us_stock": us_why},
            "last_trade_check_forex": fx_na,
            "last_trade_check_us": last_us,
            "last_trade_check_result": (
                f"forex_next={fx_na!s}; "
                f"us_reason={last_us_reason!s}; "
                f"orders_us={last_orders_us}"
            ),
            "last_forex_loop_blockers": last_forex_blockers,
            "monitor_note": monitor_note,
        }
        last_summary = payload
        _write_state(root, payload)

        if once or stop:
            break
        if max_iterations is not None and iterations >= max_iterations:
            break
        sl(interval)

    final = dict(last_summary)
    final["bot_running"] = False
    final["bot_runtime"] = "STOPPED"
    final["stopped_utc"] = datetime.now(timezone.utc).isoformat()
    _write_state(root, final)
    return final


__all__ = [
    "CONFIG_RELPATH",
    "STATE_RELPATH",
    "build_ict_1m_continuous_ui_context",
    "load_ict_1m_continuous_config",
    "melbourne_window_allowed",
    "run_ict_1m_continuous_loop",
    "state_path",
]
