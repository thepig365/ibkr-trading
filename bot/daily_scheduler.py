"""Daily pre-open and opening-review scheduler (Prompt 9 Part B).

This module owns the 08:30 / 09:45 America/New_York workflow. It is
strictly **report only**: it runs the existing CLI commands (pre-open
news, market regime, dynamic watchlist, SMC scan, review queue) and
sends Telegram digests. It never executes a trade, never calls
:func:`bot.broker.Broker.place_order`, and hard-rejects any sequence
step whose command would place an order.

The live runner uses ``apscheduler.BlockingScheduler`` so
``python -m bot.cli run-scheduler`` can stay in the foreground and
print status lines. Two one-shot helpers
(:func:`run_pre_open_report_now`, :func:`run_opening_review_now`)
exist for local testing and CLI invocations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import AppConfig
from .journal import Journal
from .notifications import send_telegram_message

logger = logging.getLogger(__name__)

# Commands that could in any way place or modify orders. The
# scheduler's ``reports_only`` gate rejects any sequence step whose
# first token matches this set.
_UNSAFE_COMMANDS: frozenset[str] = frozenset({
    "place-order",
    "cancel-order",
    "modify-order",
    "enable-trading",
    "trade",
})

# Known report-only commands. Anything outside this set will log a
# warning but still run - the reports_only gate only hard-blocks the
# unsafe names above. This keeps future research commands easy to add.
_KNOWN_REPORT_COMMANDS: frozenset[str] = frozenset({
    "pre-open-news",
    "premarket-brief",
    "market-regime",
    "build-watchlist",
    "export-tws-watchlist",
    "scan-smc",
    "scan-smc-watchlist",
    "smc-review-queue",
    "portfolio",
    "reconcile",
    "open-orders",
    "test-telegram",
    "market-news-check",
    "news-monitor-readiness",
    "email-config-status",
})


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
@dataclass
class ScheduledJob:
    """Normalized schedule entry."""

    name: str
    enabled: bool
    time: str  # "HH:MM"
    days: list[str]
    command: str | None = None
    sequence: list[str] = field(default_factory=list)
    telegram: bool = False

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


def load_schedule_jobs(cfg: AppConfig) -> list[ScheduledJob]:
    """Parse ``config/schedule.yaml`` into :class:`ScheduledJob` records."""
    raw = cfg.schedule or {}
    jobs_raw = dict(raw.get("jobs") or {})
    jobs: list[ScheduledJob] = []
    for name, body in jobs_raw.items():
        if not isinstance(body, dict):
            continue
        jobs.append(
            ScheduledJob(
                name=str(name),
                enabled=bool(body.get("enabled", True)),
                time=str(body.get("time") or "00:00"),
                days=[str(d).lower() for d in (body.get("days") or [])],
                command=(str(body.get("command")) if body.get("command") else None),
                sequence=[str(s) for s in (body.get("sequence") or [])],
                telegram=bool(body.get("telegram", False)),
            )
        )
    return jobs


def schedule_timezone(cfg: AppConfig) -> ZoneInfo:
    tz_name = str((cfg.schedule or {}).get("timezone") or "America/New_York")
    return ZoneInfo(tz_name)


def schedule_safety(cfg: AppConfig) -> dict[str, bool]:
    safety = (cfg.schedule or {}).get("safety") or {}
    return {
        "reports_only": bool(safety.get("reports_only", True)),
        "execution_allowed": False,  # hard-forced
        "skip_if_tws_unavailable": bool(safety.get("skip_if_tws_unavailable", True)),
        "send_error_to_telegram": bool(safety.get("send_error_to_telegram", True)),
    }


# ---------------------------------------------------------------------------
# Safety gate for sequence commands
# ---------------------------------------------------------------------------
def ensure_report_only(command: str) -> None:
    """Raise :class:`PermissionError` if ``command`` could trade."""
    token = (command or "").strip().split()[0] if command else ""
    if token in _UNSAFE_COMMANDS:
        raise PermissionError(
            f"scheduler rejected unsafe command {token!r}: "
            "reports_only=true; execution_allowed=false"
        )
    if token and token not in _KNOWN_REPORT_COMMANDS:
        logger.warning(
            "scheduler running unknown command %r; treating as report-only. "
            "If this command executes trades, add it to _UNSAFE_COMMANDS.",
            token,
        )


# ---------------------------------------------------------------------------
# Scheduler logging
# ---------------------------------------------------------------------------
def _log_path(cfg: AppConfig, date: str | None = None) -> Path:
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = cfg.absolute(f"data/scheduler/{day}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _log_run(
    cfg: AppConfig,
    *,
    job: str,
    status: str,
    details: str = "",
    telegram_sent: bool = False,
) -> None:
    """Append a scheduler-run record. Never raises."""
    try:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "job": job,
            "status": status,
            "details": details,
            "telegram_sent": bool(telegram_sent),
            "execution_allowed": False,
        }
        with _log_path(cfg).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:  # noqa: BLE001 - logging must not crash scheduler
        logger.warning("scheduler log append failed: %s", exc)


# ---------------------------------------------------------------------------
# Job runners
# ---------------------------------------------------------------------------
CommandFn = Callable[[list[str]], int]


def _default_command_runner(argv: list[str]) -> int:
    """Invoke the Typer app in-process without spawning a subprocess.

    Subprocesses would work but they pay a Python startup cost on each
    step and hide exceptions. Running through Typer's CliRunner keeps
    everything in one interpreter and lets us capture exit codes.
    """
    from typer.testing import CliRunner

    from .cli import app

    # Click 8.2 removed the ``mix_stderr`` keyword; omit it so we
    # stay compatible with both old and new releases.
    runner = CliRunner()
    result = runner.invoke(app, argv, catch_exceptions=False)
    if result.exit_code != 0:
        logger.warning(
            "scheduler step exited with code %s: argv=%s\n%s",
            result.exit_code, argv, result.stdout,
        )
    return result.exit_code


def _send_scheduler_error(
    cfg: AppConfig, journal: Journal, title: str, body: str
) -> bool:
    if not schedule_safety(cfg)["send_error_to_telegram"]:
        return False
    text = f"[WARN] {title}\n\n{body}"
    try:
        return send_telegram_message(text, cfg=cfg, journal=journal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to notify scheduler error: %s", exc)
        return False


def run_pre_open_report_now(
    cfg: AppConfig,
    journal: Journal,
    *,
    command_fn: CommandFn | None = None,
) -> dict[str, Any]:
    """Execute the 08:30 pre-open workflow immediately.

    Returns a dict suitable for the scheduler log so callers can test
    the outcome without inspecting the JSONL file.
    """
    runner = command_fn or _default_command_runner
    status = "success"
    details = "pre-open-news"
    telegram_sent = False
    try:
        rc = runner(["pre-open-news"])
        if rc != 0:
            status = "failed"
            details = f"pre-open-news exit_code={rc}"
            _send_scheduler_error(
                cfg, journal,
                "Pre-open news job failed",
                f"exit_code={rc}. See logs.",
            )
        else:
            telegram_sent = True
    except Exception as exc:  # noqa: BLE001 - surface and keep going
        status = "failed"
        details = f"pre-open-news raised: {exc!r}"
        _send_scheduler_error(
            cfg, journal,
            "Pre-open news job crashed",
            repr(exc),
        )
    _log_run(
        cfg, job="pre_open_news", status=status,
        details=details, telegram_sent=telegram_sent,
    )
    journal.record_event(
        category="scheduler",
        level="INFO" if status == "success" else "WARNING",
        message=f"pre_open_news {status}",
        payload={"details": details, "execution_allowed": False},
    )
    return {
        "job": "pre_open_news",
        "status": status,
        "details": details,
        "telegram_sent": telegram_sent,
        "execution_allowed": False,
    }


def run_opening_review_now(
    cfg: AppConfig,
    journal: Journal,
    *,
    sequence: Iterable[str] | None = None,
    command_fn: CommandFn | None = None,
) -> dict[str, Any]:
    """Execute the 09:45 opening-review sequence immediately.

    Steps come from ``schedule.yaml`` by default; pass ``sequence`` in
    tests. Each step is gated by :func:`ensure_report_only` before it
    runs so an edited schedule cannot sneak a trade command into the
    opening workflow.
    """
    if sequence is None:
        jobs = {j.name: j for j in load_schedule_jobs(cfg)}
        job = jobs.get("opening_smc_review")
        if not job or not job.sequence:
            return {
                "job": "opening_smc_review",
                "status": "skipped",
                "details": "no sequence configured",
                "telegram_sent": False,
                "execution_allowed": False,
            }
        sequence = job.sequence
    runner = command_fn or _default_command_runner

    results: list[dict[str, Any]] = []
    overall_status = "success"
    telegram_sent = False
    for step in sequence:
        try:
            ensure_report_only(step)
        except PermissionError as exc:
            results.append(
                {"step": step, "status": "rejected_unsafe", "error": str(exc)}
            )
            overall_status = "failed"
            _send_scheduler_error(
                cfg, journal,
                "Opening review rejected unsafe step",
                f"step={step!r}. reports_only=true.",
            )
            break

        argv = step.split()
        try:
            rc = runner(argv)
        except Exception as exc:  # noqa: BLE001 - continue or abort per safety
            rc = -1
            results.append(
                {"step": step, "status": "crashed", "error": repr(exc)}
            )
            overall_status = "failed"
            _send_scheduler_error(
                cfg, journal,
                f"Opening review step crashed: {step}",
                repr(exc),
            )
            if "--ibkr" in argv and schedule_safety(cfg)["skip_if_tws_unavailable"]:
                # TWS unavailable is a soft failure — log, notify, but
                # keep going so downstream report-only steps still run.
                _log_run(
                    cfg, job=f"opening_smc_review::{argv[0]}",
                    status="skipped",
                    details="TWS unavailable; continuing report-only",
                )
                continue
            break

        results.append({"step": step, "status": "success", "exit_code": rc})
        if rc != 0:
            overall_status = "failed"
        if "--telegram" in argv and rc == 0:
            telegram_sent = True

    _log_run(
        cfg, job="opening_smc_review", status=overall_status,
        details=json.dumps(results)[:2000],
        telegram_sent=telegram_sent,
    )
    journal.record_event(
        category="scheduler",
        level="INFO" if overall_status == "success" else "WARNING",
        message=f"opening_smc_review {overall_status}",
        payload={
            "steps": results,
            "execution_allowed": False,
        },
    )
    return {
        "job": "opening_smc_review",
        "status": overall_status,
        "steps": results,
        "telegram_sent": telegram_sent,
        "execution_allowed": False,
    }


# ---------------------------------------------------------------------------
# Scheduler construction & status
# ---------------------------------------------------------------------------
_DAY_MAP = {
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri",
    "sat": "sat", "sun": "sun",
}


def _day_of_week(days: list[str]) -> str:
    return ",".join(_DAY_MAP.get(d, d) for d in days) or "mon-fri"


def build_daily_scheduler(
    cfg: AppConfig,
    journal: Journal,
    *,
    scheduler_cls: type[BlockingScheduler] = BlockingScheduler,
) -> BlockingScheduler:
    """Create (do not start) a blocking scheduler for the daily jobs.

    This scheduler is separate from the interval-based one in
    :mod:`bot.scheduler`; it only handles the user-facing daily
    report-only workflow.
    """
    tz = schedule_timezone(cfg)
    scheduler = scheduler_cls(timezone=tz)
    jobs = load_schedule_jobs(cfg)
    for job in jobs:
        if not job.enabled:
            continue
        trigger = CronTrigger(
            day_of_week=_day_of_week(job.days),
            hour=job.hour,
            minute=job.minute,
            timezone=tz,
        )
        if job.name == "pre_open_news":
            scheduler.add_job(
                run_pre_open_report_now,
                trigger,
                args=[cfg, journal],
                id=job.name,
                max_instances=1,
                coalesce=True,
            )
        elif job.name == "opening_smc_review":
            scheduler.add_job(
                run_opening_review_now,
                trigger,
                args=[cfg, journal],
                id=job.name,
                max_instances=1,
                coalesce=True,
            )
        else:
            # Unknown job name — log and skip. We do not silently run
            # arbitrary commands at scheduled times.
            logger.warning(
                "schedule.yaml: unknown job %r skipped", job.name
            )
    return scheduler


@dataclass
class ScheduleStatus:
    timezone: str
    enabled: bool
    reports_only: bool
    execution_allowed: bool
    jobs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "enabled": self.enabled,
            "reports_only": self.reports_only,
            "execution_allowed": False,
            "jobs": self.jobs,
        }


def schedule_status(
    cfg: AppConfig, *, now: datetime | None = None
) -> ScheduleStatus:
    """Compute next-run metadata for every configured job.

    Pass ``now`` in tests to pin the clock. Returns a plain dataclass
    so the CLI can serialise it without touching the APScheduler
    internals.
    """
    tz = schedule_timezone(cfg)
    safety = schedule_safety(cfg)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)

    jobs: list[dict[str, Any]] = []
    for job in load_schedule_jobs(cfg):
        trig = CronTrigger(
            day_of_week=_day_of_week(job.days),
            hour=job.hour, minute=job.minute, timezone=tz,
        )
        next_run = trig.get_next_fire_time(None, current) if job.enabled else None
        jobs.append(
            {
                "name": job.name,
                "enabled": job.enabled,
                "time": job.time,
                "days": job.days,
                "command": job.command,
                "sequence": job.sequence,
                "next_run": next_run.isoformat() if next_run else None,
            }
        )

    return ScheduleStatus(
        timezone=str(tz),
        enabled=bool((cfg.schedule or {}).get("enabled", True)),
        reports_only=safety["reports_only"],
        execution_allowed=False,
        jobs=jobs,
    )


__all__ = [
    "CommandFn",
    "ScheduledJob",
    "ScheduleStatus",
    "build_daily_scheduler",
    "ensure_report_only",
    "load_schedule_jobs",
    "run_opening_review_now",
    "run_pre_open_report_now",
    "schedule_safety",
    "schedule_status",
    "schedule_timezone",
]
