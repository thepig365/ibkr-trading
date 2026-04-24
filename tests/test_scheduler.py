"""Tests for :mod:`bot.daily_scheduler` — Prompt 9 Part B.

Design guarantees verified here:
  * schedule.yaml loads with timezone America/New_York.
  * pre_open_news fires at 08:30, opening_smc_review at 09:45 Mon-Fri.
  * schedule-status reports the next run times.
  * run-pre-open-report runs only the pre-open workflow.
  * run-opening-review runs the configured sequence in order.
  * unsafe commands are hard-rejected by ``ensure_report_only``.
  * broker.place_order is never reached.
  * TWS / Telegram failures fall back cleanly without crashing.
  * execution_allowed stays False on every surface.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import daily_scheduler as ds_mod
from bot.daily_scheduler import (
    ScheduledJob,
    build_daily_scheduler,
    ensure_report_only,
    load_schedule_jobs,
    run_opening_review_now,
    run_pre_open_report_now,
    schedule_safety,
    schedule_status,
    schedule_timezone,
)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------
def test_schedule_yaml_timezone_is_new_york(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    assert str(schedule_timezone(cfg)) == "America/New_York"


def test_schedule_jobs_parsed(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    jobs = {j.name: j for j in load_schedule_jobs(cfg)}
    assert "pre_open_news" in jobs
    assert jobs["pre_open_news"].time == "08:30"
    assert jobs["pre_open_news"].days == [
        "mon", "tue", "wed", "thu", "fri",
    ]
    assert jobs["pre_open_news"].telegram is True

    assert "opening_smc_review" in jobs
    assert jobs["opening_smc_review"].time == "09:45"
    seq = jobs["opening_smc_review"].sequence
    assert seq[0].split()[0] == "market-regime"
    assert seq[1].split()[0] == "build-watchlist"
    # Prompt 9.3: export-tws-watchlist runs right after build-watchlist
    # so the TWS CSV/TXT are ready before the SMC scan kicks off.
    assert seq[2].split()[0] == "export-tws-watchlist"
    assert "--latest" in seq[2]
    assert "--telegram" in seq[2]
    assert seq[3].split()[0] == "scan-smc-watchlist"
    assert seq[4].split()[0] == "smc-review-queue"


def test_schedule_safety_forces_execution_off(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    safety = schedule_safety(cfg)
    assert safety["reports_only"] is True
    assert safety["execution_allowed"] is False


# ---------------------------------------------------------------------------
# ensure_report_only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", ["place-order", "trade", "enable-trading"])
def test_ensure_report_only_blocks_unsafe_commands(cmd: str) -> None:
    with pytest.raises(PermissionError):
        ensure_report_only(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "pre-open-news",
        "market-regime --ibkr",
        "build-watchlist --ibkr --limit 50",
        "scan-smc-watchlist --source dynamic --ibkr",
        "smc-review-queue --telegram --markdown",
    ],
)
def test_ensure_report_only_allows_known_research_commands(cmd: str) -> None:
    ensure_report_only(cmd)  # must not raise


# ---------------------------------------------------------------------------
# schedule_status
# ---------------------------------------------------------------------------
def test_schedule_status_shows_next_run_times(tmp_project: Path) -> None:
    from bot.config import load_config
    cfg = load_config(project_root=tmp_project)
    ny = ZoneInfo("America/New_York")
    # Pin the clock to Wednesday 08:00 NY so both jobs have a
    # deterministic next-run later the same day.
    now = datetime(2026, 4, 22, 8, 0, tzinfo=ny)
    status = schedule_status(cfg, now=now)
    assert status.timezone == "America/New_York"
    assert status.execution_allowed is False
    assert status.reports_only is True
    by_name = {j["name"]: j for j in status.jobs}
    assert by_name["pre_open_news"]["next_run"].startswith("2026-04-22T08:30")
    assert by_name["opening_smc_review"]["next_run"].startswith("2026-04-22T09:45")


# ---------------------------------------------------------------------------
# run-pre-open-report
# ---------------------------------------------------------------------------
def test_run_pre_open_report_invokes_only_pre_open_news(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    called: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        called.append(list(argv))
        return 0

    result = run_pre_open_report_now(cfg, journal, command_fn=fake_runner)
    assert result["status"] == "success"
    assert result["execution_allowed"] is False
    assert called == [["pre-open-news"]]
    # Scheduler log appended.
    log_dir = tmp_project / "data" / "scheduler"
    files = list(log_dir.glob("*.jsonl"))
    assert files, "scheduler log not written"
    entry = json.loads(files[0].read_text().strip().splitlines()[-1])
    assert entry["job"] == "pre_open_news"
    assert entry["execution_allowed"] is False


def test_run_pre_open_report_handles_crash_without_raising(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    def boom(_argv: list[str]) -> int:
        raise RuntimeError("simulated TWS crash")

    # Avoid real telegram calls during the error notification path.
    monkeypatch.setattr(
        ds_mod, "send_telegram_message",
        lambda *a, **kw: False,
    )

    result = run_pre_open_report_now(cfg, journal, command_fn=boom)
    assert result["status"] == "failed"
    assert "simulated TWS crash" in result["details"]
    assert result["execution_allowed"] is False


# ---------------------------------------------------------------------------
# run-opening-review
# ---------------------------------------------------------------------------
def test_run_opening_review_runs_sequence_in_order(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    called: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        called.append(list(argv))
        return 0

    result = run_opening_review_now(cfg, journal, command_fn=fake_runner)
    assert result["status"] == "success"
    assert result["execution_allowed"] is False
    first_tokens = [c[0] for c in called]
    assert first_tokens == [
        "market-regime", "build-watchlist", "export-tws-watchlist",
        "scan-smc-watchlist", "smc-review-queue",
    ]


def test_run_opening_review_rejects_unsafe_sequence(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    monkeypatch.setattr(
        ds_mod, "send_telegram_message", lambda *a, **kw: False
    )

    bad_sequence = ["pre-open-news", "place-order --fake"]
    called: list[list[str]] = []

    def fake_runner(argv: list[str]) -> int:
        called.append(list(argv))
        return 0

    result = run_opening_review_now(
        cfg, journal, sequence=bad_sequence, command_fn=fake_runner,
    )
    assert result["status"] == "failed"
    # Only the safe step ran; the unsafe one was rejected before
    # reaching the runner.
    assert called == [["pre-open-news"]]
    assert any(s.get("status") == "rejected_unsafe" for s in result["steps"])


def test_run_opening_review_survives_tws_crash_when_skip_flag_set(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``skip_if_tws_unavailable: true``, an IBKR step that
    crashes should not abort the sequence; the remaining report-only
    steps must continue to run."""
    from bot.config import load_config
    from bot.journal import Journal

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    monkeypatch.setattr(
        ds_mod, "send_telegram_message", lambda *a, **kw: False
    )

    def runner(argv: list[str]) -> int:
        if "--ibkr" in argv:
            raise RuntimeError("TWS down")
        return 0

    seq = [
        "market-regime --ibkr",
        "smc-review-queue --telegram --markdown",
    ]
    result = run_opening_review_now(
        cfg, journal, sequence=seq, command_fn=runner,
    )
    assert result["execution_allowed"] is False
    # The second, non-ibkr step should still have been attempted.
    steps = {s["step"]: s for s in result["steps"]}
    assert "smc-review-queue --telegram --markdown" in steps


def test_scheduler_never_reaches_place_order(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth: patch broker.Broker.place_order to blow up.

    If any scheduler code path were to reach place_order, the fake
    would raise and fail this test.
    """
    from bot import broker as broker_module
    from bot.config import load_config
    from bot.journal import Journal

    def _boom(*_a, **_kw):  # pragma: no cover - guardrail
        raise AssertionError("place_order must not be invoked")

    monkeypatch.setattr(broker_module.Broker, "place_order", _boom)
    monkeypatch.setattr(
        ds_mod, "send_telegram_message", lambda *a, **kw: False
    )

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)

    # Use a noop command_fn so the test does not invoke real CLI
    # commands; we only care that the orchestration path itself never
    # touches the broker.
    run_pre_open_report_now(cfg, journal, command_fn=lambda _a: 0)
    run_opening_review_now(cfg, journal, command_fn=lambda _a: 0)


# ---------------------------------------------------------------------------
# build_daily_scheduler
# ---------------------------------------------------------------------------
def test_build_daily_scheduler_registers_expected_jobs(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.config import load_config
    from bot.journal import Journal

    class _FakeScheduler:
        def __init__(self, timezone=None) -> None:
            self.timezone = timezone
            self.jobs: list[dict] = []

        def add_job(self, fn, trigger, *, args, id, max_instances, coalesce):
            self.jobs.append(
                {"fn": fn.__name__, "id": id, "trigger": trigger}
            )

        def start(self) -> None:  # pragma: no cover
            raise AssertionError("scheduler.start must not be called in tests")

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    scheduler = build_daily_scheduler(
        cfg, journal, scheduler_cls=_FakeScheduler,
    )
    ids = {j["id"]: j for j in scheduler.jobs}
    assert set(ids) == {"pre_open_news", "opening_smc_review"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _patch_project_root(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bot import cli as cli_module
    from bot import config as config_module

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_project)
    monkeypatch.setattr(
        cli_module, "load_config",
        lambda **kw: config_module.load_config(project_root=tmp_project, **kw),
    )


def test_cli_schedule_status_prints_ny_jobs(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_project_root(tmp_project, monkeypatch)
    from typer.testing import CliRunner
    from bot.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["schedule-status"])
    assert result.exit_code == 0, result.output
    assert "America/New_York" in result.output
    assert "pre_open_news" in result.output
    assert "opening_smc_revie" in result.output  # truncated in Rich table
    assert "08:30" in result.output
    assert "09:45" in result.output
