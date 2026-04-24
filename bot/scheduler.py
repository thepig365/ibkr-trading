"""APScheduler-backed periodic task runner.

Foundation milestone scope: only schedule SAFE, read-only jobs:
  * snapshot account + positions to the journal
  * run reconciliation and surface failures via Telegram fallback

No strategy job is registered here; that is a future milestone.
"""

from __future__ import annotations

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .broker import Broker
from .config import AppConfig
from .journal import Journal
from .news_report import (
    append_report_markdown,
    generate_report,
    notify_report,
    save_report_json,
)
from .notifications import send_telegram_message
from .reconciliation import reconcile

logger = logging.getLogger(__name__)


def _snapshot_job(broker: Broker, journal: Journal) -> None:
    try:
        summaries = broker.get_account_summary()
        for s in summaries:
            journal.record_account_snapshot(s.to_dict())
        positions = [p.to_dict() for p in broker.get_positions()]
        aid = summaries[0].account_id if summaries else ""
        journal.record_positions_snapshot(positions, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Snapshot job failed")
        journal.record_event(
            category="scheduler",
            level="ERROR",
            message="snapshot_job error",
            payload={"error": repr(exc)},
        )


def _reconcile_job(cfg: AppConfig, broker: Broker, journal: Journal) -> None:
    report = reconcile(broker, journal)
    if not report.passed:
        send_telegram_message(
            f"Reconciliation FAIL: {report.as_dict()}", cfg=cfg, journal=journal
        )


def _pre_open_news_job(cfg: AppConfig, journal: Journal) -> None:
    """Scheduled wrapper around the pre-open news report.

    The job never places orders. It produces the report, writes JSON +
    markdown, and sends a Telegram digest with fallback.
    """
    try:
        report = generate_report(cfg)
        save_report_json(cfg, report)
        append_report_markdown(cfg, report)
        notify_report(cfg, report, journal=journal)
        journal.record_event(
            category="pre_open_news",
            level="INFO" if report.new_positions_allowed else "WARNING",
            message="scheduled report generated",
            payload={
                "date": report.date,
                "regime": report.market_regime,
                "new_positions_allowed": report.new_positions_allowed,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("pre_open_news job failed")
        journal.record_event(
            category="pre_open_news",
            level="ERROR",
            message="scheduled report raised",
            payload={"error": repr(exc)},
        )


def build_scheduler(
    cfg: AppConfig,
    broker: Broker,
    journal: Journal,
    *,
    snapshot_interval_seconds: int = 300,
    reconcile_interval_seconds: int = 300,
    extra_jobs: list[tuple[str, Callable[[], None], int]] | None = None,
) -> BackgroundScheduler:
    """Create (but do not start) a scheduler with the safe default jobs.

    `extra_jobs` is a list of `(job_id, callable, interval_seconds)`
    tuples for tests; in production the foundation milestone never
    passes additional jobs.
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _snapshot_job,
        "interval",
        seconds=snapshot_interval_seconds,
        args=[broker, journal],
        id="snapshot",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _reconcile_job,
        "interval",
        seconds=reconcile_interval_seconds,
        args=[cfg, broker, journal],
        id="reconcile",
        max_instances=1,
        coalesce=True,
    )

    # Pre-open news runs at the configured America/New_York time on US
    # trading weekdays (Mon-Fri; holiday calendar is deferred). The cron
    # trigger is registered so that operators starting the scheduler
    # pick it up automatically.
    news_cfg = (cfg.news or {}).get("pre_open_news", {}) or {}
    hhmm = str(news_cfg.get("schedule_time_new_york", "08:30"))
    tz = str(news_cfg.get("timezone", "America/New_York"))
    try:
        hour, minute = (int(p) for p in hhmm.split(":", 1))
    except Exception:  # noqa: BLE001
        hour, minute = 8, 30
    scheduler.add_job(
        _pre_open_news_job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
        args=[cfg, journal],
        id="pre_open_news",
        max_instances=1,
        coalesce=True,
    )

    for job_id, fn, interval in extra_jobs or []:
        scheduler.add_job(fn, "interval", seconds=interval, id=job_id)
    return scheduler
