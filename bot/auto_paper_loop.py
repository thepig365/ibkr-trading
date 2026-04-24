"""Background loop: market regime, watchlist, MTF scan, diagnostic, auto-paper (PAPER only)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .auto_paper_mtf import (
    is_kill_switch_active,
    is_runtime_mtf_auto_disabled_explicit,
    is_runtime_mtf_auto_enabled,
    run_auto_paper_mtf,
    us_rth_allows_new_entries,
)
from .config import AppConfig
from .journal import Journal
from .notifications import send_telegram_message

logger = logging.getLogger(__name__)


def _log_loop(cfg: AppConfig, line: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def _sub_cli(cfg: AppConfig, argv: list[str]) -> int:
    r = subprocess.run(
        [sys.executable, "-m", "bot.cli"] + argv,
        cwd=str(cfg.project_root),
    )
    return int(r.returncode or 0)


def _state_path(cfg: AppConfig) -> Path:
    return cfg.absolute("data/runtime/auto_paper_loop_state.json")


def _load_state(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(p: Path, d: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_auto_paper_mtf_loop(
    cfg: AppConfig,
    journal: Journal,
    *,
    source: str = "dynamic",
    limit: int = 20,
    max_paper_trades: int = 1,
    interval_minutes: int = 5,
    market_hours_only: bool = True,
    telegram: bool = False,
    once: bool = False,
    stop_after_minutes: float | None = None,
    time_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Endless (or time-bounded) automatic paper loop."""
    t0 = time_fn()
    end = t0 + (float(stop_after_minutes) * 60.0) if stop_after_minutes else None
    cycle = 0
    st_p = _state_path(cfg)
    st = _load_state(st_p)
    log_dir = cfg.absolute("data/auto_paper_loop")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jlog = log_dir / f"{day}-loop.jsonl"
    last_hb: float = float(st.get("last_heartbeat_ts", 0) or 0)
    while end is None or time_fn() < end:
        cycle += 1
        if once and cycle > 1:
            break
        st_prev = _load_state(st_p)
        line: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "market_open": True,
            "paper_account_confirmed": False,
            "full_alignment_count": 0,
            "eligible_count": 0,
            "orders_submitted": 0,
            "status": "success",
            "reason": "",
            "execution_mode": "paper",
            "live_trading": False,
            "kill_switch": is_kill_switch_active(cfg),
            "runtime_mtf_on": is_runtime_mtf_auto_enabled(cfg),
            "runtime_mtf_off_explicit": is_runtime_mtf_auto_disabled_explicit(cfg),
        }
        if is_kill_switch_active(cfg):
            line["status"] = "skipped"
            line["reason"] = "kill_switch"
            _log_loop(cfg, line, jlog)
            if telegram and cfg.telegram.is_configured:
                send_telegram_message(
                    "<pre>auto-paper loop: KILL_SWITCH active, cycle skipped</pre>",
                    cfg=cfg,
                    journal=journal,
                )
            if once:
                break
            sleep_fn(max(1.0, interval_minutes * 60.0))
            continue
        if market_hours_only:
            ok, why = us_rth_allows_new_entries()
            line["market_open"] = ok
            if not ok:
                line["status"] = "skipped"
                line["reason"] = why
                _log_loop(cfg, line, jlog)
                if once:
                    break
                sleep_fn(max(1.0, interval_minutes * 60.0))
                continue
        try:
            # 1) market regime
            _sub_cli(cfg, ["market-regime", "--ibkr"])
            # 2) watchlist
            _sub_cli(cfg, ["build-watchlist", "--ibkr", "--limit", str(max(20, limit))])
            res = run_auto_paper_mtf(
                cfg,
                journal,
                source=source,
                limit=limit,
                max_paper_trades=max_paper_trades,
                telegram=False,
            )
            line["paper_account_confirmed"] = "paper ok" in str(
                res.preflight.get("paper_account", "")
            )
            s = res.summary or {}
            line["full_alignment_count"] = int(
                (s.get("counts") or {}).get("FULL_ALIGNMENT", 0) or 0
            )
            line["eligible_count"] = len(
                s.get("eligible_for_future_paper_trade") or []
            )
            osub = 0
            for r in s.get("mtf_paper_bracket_runs") or []:
                if (r.get("result") or {}).get("submitted"):
                    osub += 1
            line["orders_submitted"] = osub
            # 3) diagnostic (saved JSON under data/mtf_smc)
            _sub_cli(
                cfg,
                ["mtf-diagnostic-report", "--latest", "--top", "10", "--min-score", "55"],
            )
            # 4) near-alignment (no --telegram: avoid per-cycle noise; data refresh only)
            _sub_cli(cfg, ["mtf-near-alignment-alert", "--latest"])
            if res.ok and osub == 0 and line["full_alignment_count"] == 0:
                line["status"] = "success"
            if telegram and cfg.telegram.is_configured:
                now_ts = time_fn()
                do_hb = (now_ts - last_hb) >= 1800.0
                if osub > 0 or line["full_alignment_count"] > 0 or do_hb:
                    send_telegram_message(
                        f"<pre>auto-paper: cycle {cycle} FULL={line['full_alignment_count']} "
                        f"orders_submitted={osub} msg={res.message}</pre>",
                        cfg=cfg,
                        journal=journal,
                    )
                    if do_hb:
                        last_hb = now_ts
        except Exception as exc:  # noqa: BLE001
            line["status"] = "failed"
            line["reason"] = str(exc)
            line["trace"] = traceback.format_exc()[:2000]
            if telegram and cfg.telegram.is_configured:
                send_telegram_message(
                    f"<pre>auto-paper loop error: {exc!s}</pre>",
                    cfg=cfg,
                    journal=journal,
                )
        _log_loop(cfg, line, jlog)
        st = {**st_prev}
        st.update(
            {
                "last_cycle_utc": line["timestamp"],
                "last_full_alignment_count": line["full_alignment_count"],
                "last_heartbeat_ts": last_hb,
                "last_orders_submitted": line["orders_submitted"],
                "last_status": line["status"],
                "last_reason": line.get("reason", ""),
                "kill_switch": line["kill_switch"],
                "runtime_mtf_on": line["runtime_mtf_on"],
                "runtime_mtf_off_explicit": line["runtime_mtf_off_explicit"],
                "cycles": cycle,
            }
        )
        _save_state(st_p, st)
        if once:
            break
        if end is not None and time_fn() + interval_minutes * 60 >= end:
            break
        sleep_fn(float(max(1, interval_minutes * 60)))


__all__ = ["run_auto_paper_mtf_loop", "us_rth_allows_new_entries"]
