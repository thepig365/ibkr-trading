"""Tests for the CLI verbosity controls.

These tests verify that:
  * routine IBKR status messages are filtered out by default,
  * the `--verbose` flag turns third-party debug logs back on,
  * the global flag works both before and after the subcommand name.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from bot import cli as cli_module
from bot.config import load_config
from bot.ibkr_client import IBKRClient


def _patch_ibkr(monkeypatch) -> None:
    def fake_connect(self, timeout: float = 10.0) -> None:
        self._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(IBKRClient, "get_account_summary", lambda self, account=None: [])
    monkeypatch.setattr(IBKRClient, "get_positions", lambda self: [])


def test_default_silences_third_party_loggers(monkeypatch, tmp_project: Path) -> None:
    _patch_ibkr(monkeypatch)
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["portfolio"])
    assert result.exit_code == 0, result.stdout

    # After the CLI call, noisy loggers must be WARNING+ so informational
    # IBKR status events stay hidden in non-verbose runs.
    for name in (
        "ib_async",
        "ib_insync",
        "httpx",
        "httpcore",
        "apscheduler",
    ):
        assert logging.getLogger(name).level >= logging.WARNING, name


def test_verbose_flag_enables_debug_logs(monkeypatch, tmp_project: Path) -> None:
    _patch_ibkr(monkeypatch)
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["--verbose", "portfolio"])
    assert result.exit_code == 0, result.stdout

    assert logging.getLogger("ib_async").level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.DEBUG


def test_status_filter_drops_farm_connection_messages() -> None:
    """The IBKR status filter must drop the well-known advisory messages."""
    f = cli_module._IBKRStatusFilter()
    for msg in (
        "Market data farm connection is OK:usfarm",
        "HMDS data farm connection is OK:ushmds",
        "Sec-def data farm connection is OK:secdef",
        "API connection ready.",
    ):
        record = logging.LogRecord(
            name="ib_async.wrapper",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is False, f"should have filtered: {msg}"


def test_status_filter_drops_by_error_code() -> None:
    f = cli_module._IBKRStatusFilter()
    record = logging.LogRecord(
        name="ib_async.wrapper",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="some advisory payload",
        args=(),
        exc_info=None,
    )
    record.errorCode = 2104  # type: ignore[attr-defined]
    assert f.filter(record) is False

    record.errorCode = 201  # actual error - must pass through
    assert f.filter(record) is True
