"""Full-auto paper supervisor — outer loop, gates, Telegram blockers, optional engine."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .automatic_paper_engine import run_automatic_paper_engine
from .backtests.candle_coverage import CORE_BASKET
from .config import AppConfig
from .full_auto_paper_readiness import (
    FULL_AUTO_STATE_RELPATH,
    build_full_auto_paper_readiness,
    in_premarket_check_window,
    in_trading_window_morning,
    tws_port_listening,
)
from .full_auto_telegram import (
    BLOCKER_BUDGET,
    BLOCKER_BRACKET,
    BLOCKER_IBKR_FAILED,
    BLOCKER_KILL,
    BLOCKER_LIVE,
    BLOCKER_MARKET,
    BLOCKER_NOT_PAPER,
    BLOCKER_PREFLIGHT,
    BLOCKER_RECONCILE,
    BLOCKER_STRATEGY,
    BLOCKER_TWS_NOT_LISTENING,
    BLOCKER_WINDOW,
    send_blocker_telegram_if_configured,
)
from .journal import Journal
from .intraday_entry_window import (
    entry_timezone_now_display,
    intraday_new_entries_allow_config,
    ny_premarket_compatible,
)
from .reports.market_news_check import run_market_news_check
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")

EngineRunner = Callable[..., dict[str, Any]]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ny_hhmm() -> str:
    return datetime.now(_NY).strftime("%H:%M")


def _weekday_minutes() -> tuple[int, int]:
    n = datetime.now(_NY)
    return int(n.weekday()), n.hour * 60 + n.minute


def write_full_auto_supervisor_state(
    project_root: Path,
    payload: dict[str, Any],
) -> None:
    root = Path(project_root).resolve()
    p = root / FULL_AUTO_STATE_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    cur: dict[str, Any] = {}
    if p.is_file():
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(o, dict):
                cur = o
        except (OSError, json.JSONDecodeError, TypeError):
            cur = {}
    cur.update(payload)
    cur["updated_utc"] = _now_utc_iso()
    p.write_text(json.dumps(cur, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _primary_blocker_code(blockers: list[str], *, tws_ok: bool) -> tuple[str, str]:
    if not tws_ok:
        return (
            BLOCKER_TWS_NOT_LISTENING,
            "Open TWS (paper) or IB Gateway and ensure the API port in settings matches the listening port.",
        )
    for b in blockers or []:
        s = (b or "").lower()
        if "not paper" in s or "account.mode" in s:
            return (BLOCKER_NOT_PAPER, str(b)[:400])
        if "reconcil" in s:
            return (BLOCKER_RECONCILE, str(b)[:400])
        if "0" in s and "notional" in s and "daily" in s:
            return (BLOCKER_BUDGET, str(b)[:400])
        if "kill" in s and "switch" in s:
            return (BLOCKER_KILL, str(b)[:400])
        if "ict_smc" in s or "active_paper_strategy" in s:
            return (BLOCKER_STRATEGY, str(b)[:400])
        if "live" in s and "trading" in s:
            return (BLOCKER_LIVE, str(b)[:400])
        if "market" in s and "order" in s:
            return (BLOCKER_MARKET, str(b)[:400])
        if "bracket" in s and "invariant" in s:
            return (BLOCKER_BRACKET, str(b)[:400])
        if "ibkr" in s or "probe" in s:
            return (BLOCKER_IBKR_FAILED, str(b)[:400])
    if blockers:
        return (BLOCKER_PREFLIGHT, str(blockers[0])[:400])
    return (BLOCKER_WINDOW, "Outside automatic session or gates not met.")


def _maybe_telegram_blockers(
    cfg: AppConfig,
    journal: Journal | None,
    root: Path,
    blockers: list[str],
    *,
    tws_ok: bool,
    want_telegram: bool,
    ny_t: str,
) -> str | None:
    if not want_telegram or not blockers:
        return None
    code, detail = _primary_blocker_code(blockers, tws_ok=tws_ok)
    sent = send_blocker_telegram_if_configured(
        cfg,
        journal,
        project_root=root,
        blocker_code=code,
        human_detail=f"Action: {detail}",
        ny_hhmm=ny_t,
    )
    return "sent" if sent else "skipped_or_failed"


def run_full_auto_paper_supervisor(
    cfg: AppConfig,
    journal: Journal,
    *,
    session: str = "full",
    telegram: bool = True,
    report_on_exit: bool = True,
    once: bool = False,
    dry_run: bool = False,
    sleep_seconds: float = 60.0,
    market_open_check_only: bool = False,
    no_trade: bool = False,
    news_only: bool = False,
    max_runtime_minutes: float | None = None,
    engine_runner: EngineRunner | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    root = Path(cfg.project_root).resolve()
    runner = engine_runner or run_automatic_paper_engine
    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        sess = "full"

    t0 = time_fn()
    out: dict[str, Any] = {
        "supervisor": "run_full_auto_paper_supervisor",
        "session": sess,
        "dry_run": bool(dry_run),
        "iterations": 0,
        "last_readiness": None,
        "engine_result": None,
        "news_checks": 0,
    }
    end_ts = t0 + float(max_runtime_minutes) * 60.0 if max_runtime_minutes else None
    last_news = 0.0

    def _elapsed_min() -> float:
        return (time_fn() - t0) / 60.0

    want_tg = bool(telegram) and bool(cfg.telegram.is_configured)

    while end_ts is None or time_fn() < end_ts:
        out["iterations"] = int(out["iterations"]) + 1
        ny_t = _ny_hhmm()
        nw, nm = _weekday_minutes()
        ip_loop = cfg.settings.trading.intraday_paper
        _, _, ew, _ = entry_timezone_now_display(ip_loop)
        if sess == "morning":
            in_win = in_trading_window_morning(nw, nm)
            in_pre = in_premarket_check_window(nw, nm)
            weekend = nw >= 5
        else:
            in_win, _ = intraday_new_entries_allow_config(ip_loop)
            in_pre = ny_premarket_compatible(ip_loop, ny_weekday=nw, ny_minute=nm)
            weekend = ew >= 5
        preprobe = (in_win or in_pre) and not dry_run

        rd = build_full_auto_paper_readiness(
            root, cfg, journal, probe_ibkr=preprobe, session=sess
        )
        out["last_readiness"] = rd
        host = str(getattr(cfg.ibkr, "host", "127.0.0.1") or "127.0.0.1")
        try:
            port = int(getattr(cfg.ibkr, "port", 7497) or 7497)
        except (TypeError, ValueError):
            port = 7497
        tws_ok = tws_port_listening(host, port)
        blockers = list(rd.get("blockers", []) or [])

        if market_open_check_only:
            write_full_auto_supervisor_state(
                root,
                {
                    "supervisor_phase": "check_only",
                    "current_ny_hhmm": ny_t,
                    "status": rd.get("status"),
                    "last_blocker": blockers[0] if blockers else None,
                    "in_trading_window": rd.get("in_trading_window"),
                    "engine_running": False,
                    "tws_listening_last": tws_ok,
                },
            )
            return {**out, "check_only": True, "readiness": rd}

        if dry_run:
            write_full_auto_supervisor_state(
                root,
                {
                    "supervisor_phase": "dry_run",
                    "status": rd.get("status"),
                    "last_blocker": blockers[0] if blockers else None,
                    "engine_running": False,
                    "tws_listening_last": tws_ok,
                },
            )
            return {**out, "finished": True, "readiness": rd}

        # Deduped blocker Telegram: relevant when we would trade or are in premarket / session
        # Weekday ~08:30–16:30 NY: alert TWS/gates; no overnight spam
        alert_band = nw < 5 and (8 * 60 + 30) <= nm <= (16 * 60 + 30)

        if getattr(cfg.settings.trading.tws_health_alerts, "enabled", True) and alert_band:
            try:
                from .tws_health_alerts import (  # noqa: PLC0415
                    check_tws_health_for_alerts,
                    maybe_send_tws_health_alert,
                )

                pj = journal if preprobe else None
                hs = check_tws_health_for_alerts(cfg, pj)
                maybe_send_tws_health_alert(
                    cfg,
                    journal,
                    hs,
                    source="full-auto supervisor",
                    send_telegram=want_tg,
                )
            except Exception as exc:
                logger.warning("tws health alert (supervisor, non-fatal): %s", exc)

        if want_tg and blockers and alert_band:
            st = _maybe_telegram_blockers(
                cfg,
                journal,
                root,
                blockers,
                tws_ok=tws_ok,
                want_telegram=True,
                ny_t=ny_t,
            )
            write_full_auto_supervisor_state(
                root,
                {
                    "last_telegram_blocker": st,
                    "last_blocker": blockers[0] if blockers else None,
                    "tws_listening_last": tws_ok,
                },
            )

        if news_only:
            nr = cfg.settings.news_reporting
            mscore = int(nr.min_market_moving_score)
            r = run_market_news_check(
                root,
                cfg,
                journal,
                symbols=list(CORE_BASKET),
                market_moving_only=True,
                lookback_minutes=90,
                min_score=mscore,
                want_telegram=bool(want_tg and nr.telegram_enabled),
                want_email=False,
                dry_run=not (want_tg and nr.telegram_enabled),
            )
            out["news_checks"] = int(out["news_checks"]) + 1
            out["last_news"] = r
            if once:
                return {**out, "finished": True}
            sleep_fn(float(sleep_seconds))
            continue

        # Hourly market news during premarket or RTH (no spam — run_market_news_check dedups)
        news_weekday_ok = ew < 5 if sess == "full" else nw < 5
        if (in_pre or in_win) and news_weekday_ok:
            if time_fn() - last_news >= 3600.0 or last_news == 0.0:
                nr = cfg.settings.news_reporting
                if nr.enabled and bool(nr.hourly_market_news_check):
                    mscore = int(nr.min_market_moving_score)
                    run_market_news_check(
                        root,
                        cfg,
                        journal,
                        symbols=list(CORE_BASKET),
                        market_moving_only=True,
                        lookback_minutes=90,
                        min_score=mscore,
                        want_telegram=bool(want_tg and nr.telegram_enabled),
                        want_email=False,
                        dry_run=not (want_tg and nr.telegram_enabled),
                    )
                    out["news_checks"] = int(out["news_checks"]) + 1
                last_news = time_fn()

        if not in_win or weekend:
            write_full_auto_supervisor_state(
                root,
                {
                    "supervisor_phase": "waiting",
                    "status": rd.get("status"),
                    "current_ny_hhmm": ny_t,
                    "engine_running": False,
                    "tws_listening_last": tws_ok,
                },
            )
            if once:
                return {**out, "finished": True, "note": "outside_trading_window"}
            sleep_fn(float(sleep_seconds))
            continue

        if not rd.get("ok") or no_trade:
            write_full_auto_supervisor_state(
                root,
                {
                    "supervisor_phase": "blocked" if not no_trade else "no_trade",
                    "status": rd.get("status"),
                    "last_blocker": blockers[0] if blockers else "no_trade",
                    "engine_running": False,
                    "tws_listening_last": tws_ok,
                },
            )
            if once:
                return {**out, "finished": True, "blocked": True, "no_trade": bool(no_trade)}
            sleep_fn(float(sleep_seconds))
            continue

        # Start engine (blocking)
        write_full_auto_supervisor_state(
            root,
            {
                "supervisor_phase": "engine",
                "engine_running": True,
                "status": "running",
                "last_action": "starting_automatic_paper_engine",
            },
        )
        eng: dict[str, Any] | None = None
        try:
            eng = runner(
                cfg,
                journal,
                session=sess,
                source="dynamic",
                limit=20,
                interval_seconds=max(5, int(sleep_seconds)),
                market_hours_only=True,
                telegram=want_tg,
                report_on_exit=bool(report_on_exit),
                dry_run=False,
                max_cycles=None,
                once=False,
                stop_after_minutes=None,
                turn_runtime_on=True,
                preflight_probe_ibkr=True,
            )
            out["engine_result"] = eng
        finally:
            write_full_auto_supervisor_state(
                root,
                {
                    "engine_running": False,
                    "supervisor_phase": "post_engine",
                    "last_action": "engine_exited",
                    "last_engine_summary": (eng or {}).get("post_exit")
                    if isinstance(eng, dict)
                    else None,
                },
            )
        # One engine session per process (inner loop runs until stop / EOD)
        return {**out, "finished": True}

    return {**out, "finished": True, "note": "max_runtime"}


__all__ = [
    "write_full_auto_supervisor_state",
    "run_full_auto_paper_supervisor",
]
