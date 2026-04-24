"""Tests for `bot.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bot.config import load_config


def test_default_config_loads(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)

    assert cfg.settings.account.mode == "paper"
    assert cfg.settings.account.block_live_trading is True
    assert cfg.settings.trading.enabled is False
    assert cfg.settings.trading.dry_run_default is True
    assert cfg.settings.trading.require_manual_confirmation is True
    assert cfg.settings.trading.allow_options is False
    assert cfg.settings.trading.allow_crypto is False
    assert cfg.settings.trading.allow_forex is False
    assert cfg.settings.trading.allow_shorting is False
    assert cfg.settings.risk.max_open_positions == 5
    assert cfg.settings.notifications.telegram.enabled is True
    # No env -> Telegram is unconfigured but the bot does not crash.
    assert cfg.telegram.is_configured is False


def test_paper_mode_enforced(tmp_project: Path, write_yaml) -> None:
    cfg = load_config(project_root=tmp_project)
    assert cfg.settings.account.mode == "paper"
    assert cfg.ibkr.account_mode == "paper"  # default in absence of env


def test_live_mode_hard_rejected_by_client(tmp_project: Path, write_yaml, monkeypatch) -> None:
    # Force settings to live mode while keeping block_live_trading on.
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["account"]["mode"] = "live"
    write_yaml(settings_path, settings)
    monkeypatch.setenv("IBKR_ACCOUNT_MODE", "live")

    cfg = load_config(project_root=tmp_project)
    assert cfg.settings.account.block_live_trading is True

    from bot.ibkr_client import IBKRClient, LiveTradingBlocked

    client = IBKRClient(cfg)
    with pytest.raises(LiveTradingBlocked):
        client.connect()


def test_live_port_rejected_even_if_mode_paper(tmp_project: Path, monkeypatch) -> None:
    # Even with mode=paper, a live port (7496) must be refused.
    monkeypatch.setenv("IBKR_PORT", "7496")
    cfg = load_config(project_root=tmp_project)

    from bot.ibkr_client import IBKRClient, LiveTradingBlocked

    client = IBKRClient(cfg)
    with pytest.raises(LiveTradingBlocked):
        client.connect()
