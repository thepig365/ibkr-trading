"""CLI surface for ``complete-trade-charts``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_complete_trade_charts_cli_latest_dry_run_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bot.cli",
            "complete-trade-charts",
            "--latest",
            "--dry-run",
            "--json",
            "--limit",
            "3",
        ],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    text = proc.stdout
    start = text.find("{")
    assert start >= 0, text
    depth = 0
    end = None
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None
    summary = json.loads(text[start:end])
    assert "selected_count" in summary
    assert summary.get("dry_run") is True


def test_complete_trade_charts_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bot.cli", "complete-trade-charts", "--help"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "complete-trade-charts" in proc.stdout or len(proc.stdout) > 20
