"""Background loop for ICT/SMC intraday paper bracket forward-testing (13F).

This module wraps :func:`bot.execution.intraday_paper_execution.run_intraday_paper_pass`
in a polling loop. The actual scanner / broker / order code lives in the
execution module — the loop here just handles scheduling, market hours,
heartbeat throttling, and JSONL audit lines.

Hard rules (defence-in-depth, also enforced by execution + broker):

* paper account only; no live route
* every cycle re-checks ``data/KILL_SWITCH``
* every cycle re-checks ``data/runtime/intraday_auto_paper_enabled`` and
  ``trading.intraday_paper.enabled``
* market-hours-only (default ON) gates all submissions to RTH
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .auto_paper_mtf import is_kill_switch_active as _shared_is_kill_switch_active
from .ny_session_windows import us_morning_paper_window_allows, us_rth_allows_new_entries
from .config import AppConfig
from .execution.intraday_paper_execution import (
    INTRADAY_LOOP_STATE_RELPATH,
    is_intraday_paper_runtime_enabled,
    run_intraday_paper_pass,
)
from .journal import Journal
from .notifications import send_telegram_message

logger = logging.getLogger(__name__)


def _log_loop(line: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def run_auto_paper_intraday_loop(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str = "dynamic",
    limit: int = 20,
    interval_seconds: int = 60,
    market_hours_only: bool = True,
    telegram: bool = False,
    once: bool = False,
    stop_after_minutes: float | None = None,
    heartbeat_minutes: int = 30,
    time_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
    session: str = "full",
) -> None:
    """Endless (or time-bounded) intraday paper loop.

    ``session``:
        * ``full`` — default NY entry window 09:45–15:30 (see ``us_rth_allows_new_entries``).
        * ``morning`` — US morning test window 09:45–11:30 NY only (paper forward-test prep).

    Telegram throttling: at most one heartbeat per ``heartbeat_minutes``;
    submissions / errors / critical skips always send (the execution module
    decides which skips qualify as "critical").
    """
    sess = (session or "full").strip().lower()
    if sess not in {"full", "morning"}:
        raise ValueError("session must be 'full' or 'morning'")
    t0 = time_fn()
    end = t0 + (float(stop_after_minutes) * 60.0) if stop_after_minutes else None
    cycle = 0
    log_dir = cfg.absolute("data/auto_paper_loop")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jlog = log_dir / f"{day}-intraday-loop.jsonl"
    last_hb: float = 0.0
    interval_sec = max(1.0, float(interval_seconds))
    hb_seconds = max(60.0, float(heartbeat_minutes) * 60.0)

    state_path = cfg.absolute(INTRADAY_LOOP_STATE_RELPATH)
    while end is None or time_fn() < end:
        cycle += 1
        if once and cycle > 1:
            break
        runtime_on, runtime_explicit_off = is_intraday_paper_runtime_enabled(cfg)
        kill = _shared_is_kill_switch_active(cfg)
        line: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "execution_mode": "paper",
            "live_trading": False,
            "kill_switch": kill,
            "runtime_intraday_on": runtime_on,
            "runtime_intraday_off_explicit": runtime_explicit_off,
            "market_open": True,
            "status": "success",
            "reason": "",
            "orders_submitted": 0,
        }

        if kill:
            line["status"] = "skipped"
            line["reason"] = "kill_switch"
            _log_loop(line, jlog)
            if telegram and cfg.telegram.is_configured:
                try:
                    send_telegram_message(
                        "<pre>auto-paper intraday: KILL_SWITCH active, cycle skipped</pre>",
                        cfg=cfg,
                        journal=journal,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("intraday telegram kill notice failed", exc_info=True)
            if once:
                break
            sleep_fn(interval_sec)
            continue

        if market_hours_only:
            if sess == "morning":
                ok, why = us_morning_paper_window_allows()
            else:
                ok, why = us_rth_allows_new_entries()
            line["session"] = sess
            line["market_open"] = ok
            if not ok:
                line["status"] = "skipped"
                line["reason"] = why
                _log_loop(line, jlog)
                if once:
                    break
                sleep_fn(interval_sec)
                continue

        try:
            result = run_intraday_paper_pass(
                cfg,
                journal,
                source=source,
                limit=limit,
                telegram=telegram,
                chart=False,
            )
            line["orders_submitted"] = int(result.orders_submitted)
            line["status"] = result.last_status or "success"
            line["reason"] = result.last_reason or ""
            line["strict_ready_count"] = int(result.strict_ready_count)
            line["aggressive_ready_count"] = int(result.aggressive_ready_count)
            line["symbols_scanned"] = list(result.symbols_scanned)
            line["audit_log_path"] = result.audit_log_path
            line["state_file_path"] = result.state_file_path
            if telegram and cfg.telegram.is_configured:
                now_ts = time_fn()
                do_hb = (now_ts - last_hb) >= hb_seconds
                if line["orders_submitted"] > 0 or do_hb:
                    try:
                        send_telegram_message(
                            (
                                f"<pre>intraday paper: cycle {cycle} "
                                f"strict={line['strict_ready_count']} "
                                f"aggr={line['aggressive_ready_count']} "
                                f"orders={line['orders_submitted']} "
                                f"reason={line['reason'] or '-'}</pre>"
                            ),
                            cfg=cfg,
                            journal=journal,
                        )
                        if do_hb:
                            last_hb = now_ts
                    except Exception:  # noqa: BLE001
                        logger.warning("intraday telegram digest failed", exc_info=True)
        except KeyboardInterrupt:
            line["status"] = "interrupted"
            line["reason"] = "KeyboardInterrupt"
            _log_loop(line, jlog)
            raise
        except Exception as exc:  # noqa: BLE001
            line["status"] = "failed"
            line["reason"] = str(exc)
            line["trace"] = traceback.format_exc()[:2000]
            if telegram and cfg.telegram.is_configured:
                try:
                    send_telegram_message(
                        f"<pre>auto-paper intraday loop error: {exc!s}</pre>",
                        cfg=cfg,
                        journal=journal,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("intraday telegram error notice failed", exc_info=True)

        line["loop_state_path"] = str(state_path)
        _log_loop(line, jlog)

        if once:
            break
        if end is not None and time_fn() + interval_sec >= end:
            break
        sleep_fn(interval_sec)


__all__ = ["run_auto_paper_intraday_loop"]
