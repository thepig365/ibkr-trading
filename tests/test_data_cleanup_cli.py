"""CLI ``data-status`` and ``data-cleanup`` return zero without deleting in dry-run."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def test_cli_data_status_runs() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "bot.cli", "data-status", "--json"],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "data/paper_orders" in r.stdout or "paper_orders" in r.stdout


def test_cli_data_cleanup_dry_run_runs() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "bot.cli", "data-cleanup", "--dry-run"],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "data-cleanup" in r.stdout or "nothing eligible" in r.stdout.lower() or "would" in r.stdout.lower()
