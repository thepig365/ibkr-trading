"""EOD paper report / checklist (G-3): no live broker in unit tests; file-based paths."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from bot.config import load_config
from bot.reports.paper_daily import build_daily_paper_report

REPO = Path(__file__).resolve().parent.parent


def _install_minimal_project(tmp: Path) -> None:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.yaml",
        "strategy.yaml",
        "watchlist.yaml",
        "news.yaml",
        "schedule.yaml",
        "telegram.yaml",
        "strategy_ui.yaml",
    ):
        src = REPO / "config" / name
        if src.is_file():
            shutil.copy(src, tmp / "config" / name)
    (tmp / "data").mkdir(parents=True, exist_ok=True)


def test_build_daily_paper_report_has_eod_friendly_keys(tmp_path: Path) -> None:
    _install_minimal_project(tmp_path)
    cfg = load_config(project_root=tmp_path)
    d = "2026-01-15"
    rep = build_daily_paper_report(tmp_path, d)
    assert rep.get("date") == d
    ex = rep.get("execution_summary") or {}
    assert "incomplete_bracket_count" in ex
    assert "safety" in rep and "reconcile_status" in (rep.get("safety") or {})


def test_paper_daily_report_cli_help_mentions_email() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "paper-daily-report", "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--email" in proc.stdout
    assert "--latest" in proc.stdout


def test_eod_sequence_documented_in_cli() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "eod-paper-checklist"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "paper-daily-report" in proc.stdout
