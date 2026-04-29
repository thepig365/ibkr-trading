"""CLI tws-health-alert-check help text only (full invoke needs project + mypy scope)."""

from __future__ import annotations

from typer.testing import CliRunner


def test_help_lists_tws_health_command() -> None:
    from bot.cli import app as cli_app

    r = CliRunner().invoke(cli_app, ["--help"])
    assert r.exit_code == 0
    assert "tws-health-alert-check" in (r.stdout or "")


def test_status_as_dict_has_no_secrets() -> None:
    from bot.tws_health_alerts import TWSHealthStatus, status_as_dict

    s = TWSHealthStatus(
        raw_error_safe="no KEY=here",
        checked_at_utc="2026-04-27T12:00:00Z",
        status="healthy",
    )
    out = status_as_dict(s)
    assert isinstance(out, dict)
    assert out.get("raw_error_safe") == "no KEY=here"
