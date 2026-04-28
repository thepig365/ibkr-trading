"""Automatic ICT/SMC intraday paper engine (terminal + optional UI trigger).

Heavy imports (loop, broker path) are deferred to runtime; preflight lives in
:mod:`bot.automatic_paper_preflight` for UI-safe imports.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .automatic_paper_preflight import (
    REF_MAX_DAILY_NOTIONAL_USD,
    REF_MAX_NOTIONAL_PER_ORDER_USD,
    build_automatic_paper_engine_preflight,
)
from .config import AppConfig
from .journal import Journal

logger = logging.getLogger(__name__)


@dataclass
class _EngineRunStats:
    had_orders: bool = False
    had_incomplete_bracket: bool = False
    had_broker_error: bool = False
    had_reconcile_skip: bool = False
    cycles: int = 0
    last_skipped: str = ""
    last_status: str = ""
    last_reason: str = ""


def _engine_post_exit_report(
    cfg: AppConfig,
    *,
    telegram: bool,
    had_activity: bool,
) -> dict[str, Any]:
    from .notifications import send_telegram_message  # noqa: PLC0415
    from .reports.paper_daily import build_daily_paper_report  # noqa: PLC0415
    from .reports.render_markdown import format_paper_daily_telegram_zh  # noqa: PLC0415
    from .reports.report_paths import infer_latest_report_date, utc_today_str  # noqa: PLC0415

    root = Path(cfg.project_root)
    d = infer_latest_report_date(root)
    if not d:
        d = utc_today_str()
    payload = build_daily_paper_report(root, d)
    outd = root / "data" / "reports" / "paper"
    outd.mkdir(parents=True, exist_ok=True)
    stem = f"{d}-paper-daily-report"
    jp = outd / f"{stem}.json"
    jp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    trade_charts_batch: dict[str, Any] | None = None
    try:
        from .trade_chart_completion import complete_trade_charts  # noqa: PLC0415

        tc_opt = getattr(cfg.settings.trading, "trade_charts", None)
        auto_on = bool(getattr(tc_opt, "auto_generate_on_report_exit", True)) if tc_opt else True
        if auto_on:
            lim = int(getattr(tc_opt, "max_trades_per_run", 50) if tc_opt else 50)
            fetch_on = bool(getattr(tc_opt, "fetch_missing_candles_on_report_exit", False) if tc_opt else False)
            trade_charts_batch = complete_trade_charts(
                root,
                latest=True,
                limit=lim,
                fetch_missing_candles=fetch_on,
                cfg=cfg,
            )
        else:
            trade_charts_batch = {"skipped": "auto_generate_on_report_exit_false"}
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        logger.warning("post-exit trade chart batch skipped", exc_info=True)
        trade_charts_batch = {"error": "batch_unavailable"}

    tc_suffix = ""
    if isinstance(trade_charts_batch, dict) and "error" not in trade_charts_batch and not trade_charts_batch.get(
        "skipped"
    ):
        av = int(trade_charts_batch.get("available_count") or 0)
        g = int(trade_charts_batch.get("generated_count") or 0)
        mc = int(trade_charts_batch.get("missing_candles_count") or 0)
        ne = int(trade_charts_batch.get("no_exit_count") or 0)
        mode = str(trade_charts_batch.get("mode") or "local_only")
        fetch_note = "" if mode == "local_only" else " (IBKR read-only fetch may have been used)"
        if av or g or mc:
            tc_suffix = (
                f"\nTrade charts: {av} available · {g} generated · "
                f"{mc} missing candles · {ne} no-exit labels{fetch_note}."
            )

    if telegram and had_activity and cfg.telegram.is_configured:
        try:
            body = format_paper_daily_telegram_zh(payload)[:3200]
            if tc_suffix:
                body = (body + tc_suffix)[:3900]
            send_telegram_message(
                body,
                cfg=cfg,
                journal=None,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("EOD telegram digest failed", exc_info=True)
    return {
        "report_date": d,
        "json_path": str(jp),
        "submitted_today": payload.get("summary", {}).get("submitted_to_broker_count"),
        "trade_charts_batch": trade_charts_batch,
    }


def run_automatic_paper_engine(
    cfg: AppConfig,
    journal: Journal,
    *,
    session: str = "full",
    source: str = "dynamic",
    limit: int = 20,
    interval_seconds: int = 60,
    market_hours_only: bool = True,
    telegram: bool = False,
    report_on_exit: bool = True,
    dry_run: bool = False,
    max_cycles: int | None = None,
    once: bool = False,
    stop_after_minutes: float | None = None,
    turn_runtime_on: bool = True,
    preflight_probe_ibkr: bool = True,
) -> dict[str, Any]:
    from .auto_paper_intraday_loop import run_auto_paper_intraday_loop  # noqa: PLC0415
    from .full_auto_telegram import (  # noqa: PLC0415
        format_engine_started_telegram,
        format_engine_stopped_telegram,
    )
    from .notifications import send_telegram_message  # noqa: PLC0415
    from .paper_activation import set_intraday_runtime_flag  # noqa: PLC0415

    out: dict[str, Any] = {
        "started": False,
        "finished": False,
        "dry_run": bool(dry_run),
        "blockers": [],
    }
    j_for_probe: Journal | None = journal if preflight_probe_ibkr else None
    pf = build_automatic_paper_engine_preflight(
        cfg, j_for_probe, probe_ibkr=preflight_probe_ibkr
    )
    out["preflight"] = pf

    ip = cfg.settings.trading.intraday_paper
    if bool(ip.dry_run) and not dry_run:
        out["blockers"] = list(pf.get("blockers", [])) + [
            "trading.intraday_paper.dry_run is true — disable for real bracket tests",
        ]
        return out

    if not bool(pf.get("ok", False)):
        out["blockers"] = list(pf.get("blockers", []))
        return out

    if dry_run:
        out["finished"] = True
        return out

    if turn_runtime_on:
        set_intraday_runtime_flag(cfg, on=True)

    stats = _EngineRunStats()
    if telegram and cfg.telegram.is_configured:
        try:
            send_telegram_message(
                format_engine_started_telegram(session=session),
                cfg=cfg,
                journal=journal,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("engine start telegram failed", exc_info=True)
    out["started"] = True

    def _hook(r: Any) -> None:
        stats.cycles += 1
        stats.last_status = str(getattr(r, "last_status", "") or "")
        stats.last_reason = str(getattr(r, "last_reason", "") or "")
        if "reconcil" in stats.last_reason.lower() and "fail" in stats.last_reason.lower():
            stats.had_reconcile_skip = True
        for s in list(getattr(r, "submissions", []) or []):
            if bool(getattr(s, "submitted", False)):
                stats.had_orders = True
            if bool(getattr(s, "submitted_to_broker", False)) and not bool(
                getattr(s, "submitted", False)
            ):
                stats.had_incomplete_bracket = True
            if getattr(s, "broker_errors", None):
                if list(getattr(s, "broker_errors", []) or []):
                    stats.had_broker_error = True
        lr = stats.last_reason.lower()
        if "notional" in lr and ("cap" in lr or "daily" in lr or "limit" in lr):
            stats.had_broker_error = True

    had_activity = False
    try:
        run_auto_paper_intraday_loop(
            cfg,
            journal,
            source=source,
            limit=limit,
            interval_seconds=interval_seconds,
            market_hours_only=market_hours_only,
            telegram=telegram,
            once=once,
            stop_after_minutes=stop_after_minutes,
            heartbeat_minutes=9999,
            session=session,
            telegram_style="engine",
            max_cycles=max_cycles,
            cycle_result_hook=_hook,
        )
    except KeyboardInterrupt:
        out["interrupted"] = True
    finally:
        had_activity = (
            stats.had_orders
            or stats.had_incomplete_bracket
            or stats.had_broker_error
            or stats.had_reconcile_skip
        )
        if telegram and cfg.telegram.is_configured:
            try:
                send_telegram_message(
                    format_engine_stopped_telegram(reason="loop ended or interrupt"),
                    cfg=cfg,
                    journal=journal,
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning("engine stop telegram failed", exc_info=True)
        rpt: dict[str, Any] | None = None
        if report_on_exit:
            rpt = _engine_post_exit_report(
                cfg, telegram=telegram, had_activity=had_activity
            )
        out["post_exit"] = {
            "report": rpt,
            "stats": {
                "cycles": stats.cycles,
                "had_orders": stats.had_orders,
                "had_incomplete_bracket": stats.had_incomplete_bracket,
                "had_broker_error": stats.had_broker_error,
                "last_status": stats.last_status,
                "last_reason": stats.last_reason,
            },
        }
        out["finished"] = True

    return out


__all__ = [
    "REF_MAX_DAILY_NOTIONAL_USD",
    "REF_MAX_NOTIONAL_PER_ORDER_USD",
    "build_automatic_paper_engine_preflight",
    "run_automatic_paper_engine",
]
