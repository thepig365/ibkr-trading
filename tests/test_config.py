"""Configuration loader tests."""

from __future__ import annotations

import os
from pathlib import Path

from backend.config import load_config


def test_config_loads_with_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "test_finnhub")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234567")
    monkeypatch.setenv("IBKR_ACCOUNT", "DU000001")

    config_path = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = load_config(config_path)

    assert cfg.ibkr.port == 7497
    assert cfg.connection.auto_disconnect_minutes == 30
    assert cfg.strategy == "ICT"
    assert "SPY" in cfg.symbols
    assert cfg.risk.daily_capital_limit == 100000
    assert cfg.finnhub.api_key == "test_finnhub"
    assert cfg.telegram.bot_token == "test_token"
    assert cfg.ibkr.account == "DU000001"
    assert cfg.ibkr.allow_live_trading is False
