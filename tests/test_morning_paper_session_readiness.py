"""Morning paper session flags (G-2): no loop start, no orders in these tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bot.ny_session_windows import us_morning_paper_window_allows
from bot.auto_paper_intraday_loop import run_auto_paper_intraday_loop
from bot_ui.services.safety import ALLOWED_COMMANDS, validate_args_for

REPO = Path(__file__).resolve().parent.parent


def test_run_auto_paper_intraday_loop_accepts_session_kwarg() -> None:
    """Loop module supports session= for future morning smoke (not started here)."""
    import inspect

    sig = inspect.signature(run_auto_paper_intraday_loop)
    assert "session" in sig.parameters


def test_run_auto_paper_intraday_loop_help_lists_session() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "run-auto-paper-intraday-loop", "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--session" in proc.stdout
    assert "morning" in proc.stdout


def test_us_morning_window_function_exists() -> None:
    ok, why = us_morning_paper_window_allows()
    assert isinstance(ok, bool)
    assert isinstance(why, str)


def test_run_auto_paper_intraday_loop_not_in_ui_allowlist() -> None:
    assert "run-auto-paper-intraday-loop" not in ALLOWED_COMMANDS


def test_eod_checklist_allowlisted_no_args_only() -> None:
    assert "eod-paper-checklist" in ALLOWED_COMMANDS
    assert validate_args_for("eod-paper-checklist", ())[0] is True
    assert validate_args_for("eod-paper-checklist", ("--json",))[0] is False


def test_paper_template_no_start_morning_loop_button() -> None:
    p = REPO / "bot_ui" / "templates" / "paper.html"
    t = p.read_text(encoding="utf-8")
    assert "Start morning loop" not in t.lower()
    assert "Check Morning Paper Readiness" in t
    assert "09:45" in t and "11:30" in t


def test_eod_paper_checklist_cli_prints_checklist() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "eod-paper-checklist"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "eod-paper-checklist" in out or "paper-daily-report" in out
    assert "open-orders" in out
    assert "paper-reconcile" in out


def test_morning_readiness_does_not_import_loop_runner() -> None:
    p = (REPO / "bot" / "auto_loop_readiness.py").read_text(encoding="utf-8")
    assert "run_auto_paper_intraday_loop" not in p
    assert "auto_paper_intraday_loop" not in p
