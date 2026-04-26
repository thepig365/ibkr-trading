"""CLI tests for paper-daily-report / paper-weekly-report (Prompt 13M)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _install_config(target: Path) -> None:
    (target / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, target / "config" / name)


def test_cli_daily_json_stdout(tmp_path: Path) -> None:
    _install_config(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["IBKR_TRADING_PROJECT_ROOT"] = str(tmp_path)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "paper-daily-report",
            "--date",
            "2026-04-01",
            "--json",
            "--no-save",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data.get("date") == "2026-04-01"


def test_cli_daily_writes_md_and_json(tmp_path: Path) -> None:
    _install_config(tmp_path)
    out = tmp_path / "data" / "reports" / "paper"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["IBKR_TRADING_PROJECT_ROOT"] = str(tmp_path)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "paper-daily-report",
            "--date",
            "2026-04-02",
            "--output-dir",
            "data/reports/paper",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    j = out / "2026-04-02-paper-daily-report.json"
    m = out / "2026-04-02-paper-daily-report.md"
    assert j.is_file() and m.is_file()


def test_cli_weekly_latest_no_crash(tmp_path: Path) -> None:
    _install_config(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["IBKR_TRADING_PROJECT_ROOT"] = str(tmp_path)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "paper-weekly-report",
            "--latest",
            "--json",
            "--no-save",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert "week_start" in data


def test_telegram_flag_does_not_crash_without_credentials(tmp_path: Path) -> None:
    _install_config(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["IBKR_TRADING_PROJECT_ROOT"] = str(tmp_path)
    # Ensure no accidental success requires real TG
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_CHAT_ID", None)
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "paper-daily-report",
            "--date",
            "2026-04-03",
            "--telegram",
            "--no-save",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
