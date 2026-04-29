"""CLI + safety validators for reconcile-fills (no live TWS required)."""

from __future__ import annotations

from pathlib import Path

from bot_ui.services.safety import validate_args_for


def test_validate_reconcile_fills_accepts_latest_json() -> None:
    ok, err = validate_args_for(
        "reconcile-fills", ("--latest", "--json")
    )
    assert ok and err == "", err


def test_validate_reconcile_fills_accepts_date_json() -> None:
    ok, err = validate_args_for(
        "reconcile-fills", ("--date", "2026-04-28", "--json")
    )
    assert ok and err == "", err


def test_validate_reconcile_fills_rejects_latest_and_date() -> None:
    ok, err = validate_args_for(
        "reconcile-fills",
        ("--latest", "--date", "2026-04-28", "--json"),
    )
    assert ok is False
    assert err


def test_reconcile_fills_visible_in_help() -> None:
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    p = subprocess.run(
        [sys.executable, "-m", "bot.cli", "--help"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert "reconcile-fills" in (p.stdout or "")
