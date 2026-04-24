"""Tests for the safety layer: risk_engine, broker, CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from bot.broker import Broker, ManualConfirmationRequired, TradingDisabled
from bot.config import load_config
from bot.risk_engine import RiskEngine, TradeIntent


def _make_intent(**overrides) -> TradeIntent:
    base = dict(
        symbol="AAPL",
        sec_type="STK",
        side="BUY",
        quantity=10,
        estimated_price=200.0,
    )
    base.update(overrides)
    return TradeIntent(**base)


def test_risk_engine_blocks_when_trading_disabled(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    engine = RiskEngine(cfg)
    decision = engine.evaluate(_make_intent())
    assert decision.allowed is False
    assert any("trading.enabled is false" in r for r in decision.reasons)


def test_risk_engine_blocks_options_crypto_forex_short(tmp_project: Path, write_yaml) -> None:
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["trading"]["enabled"] = True  # turn master switch on for this test
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)
    engine = RiskEngine(cfg)

    assert any("options" in r for r in engine.evaluate(_make_intent(sec_type="OPT")).reasons)
    assert any("crypto" in r for r in engine.evaluate(_make_intent(sec_type="CRYPTO")).reasons)
    assert any("forex" in r for r in engine.evaluate(_make_intent(sec_type="CASH")).reasons)
    assert any("shorting" in r for r in engine.evaluate(_make_intent(side="SHORT")).reasons)


def test_risk_engine_blocks_when_reconciliation_failed(tmp_project: Path, write_yaml) -> None:
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["trading"]["enabled"] = True
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)
    engine = RiskEngine(cfg)
    decision = engine.evaluate(_make_intent(), reconciliation_passed=False)
    assert decision.allowed is False
    assert any("reconciliation" in r for r in decision.reasons)


def test_broker_place_order_raises_when_trading_disabled(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    broker = Broker(cfg, client=MagicMock())
    with pytest.raises(TradingDisabled):
        broker.place_order(_make_intent())


def test_broker_requires_manual_confirmation(tmp_project: Path, write_yaml) -> None:
    # Enable trading but keep require_manual_confirmation=true.
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["trading"]["enabled"] = True
    settings["trading"]["dry_run_default"] = False
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)

    broker = Broker(cfg, client=MagicMock())
    with pytest.raises(ManualConfirmationRequired):
        broker.place_order(_make_intent(), confirmed=False)


def test_broker_dry_run_returns_ticket_without_submitting(tmp_project: Path, write_yaml) -> None:
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["trading"]["enabled"] = True
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)

    broker = Broker(cfg, client=MagicMock())
    ticket = broker.place_order(_make_intent(), confirmed=True)  # dry_run defaults to True
    assert ticket.dry_run is True
    assert ticket.decision.allowed is True


def test_broker_submission_path_still_disabled_in_foundation(
    tmp_project: Path, write_yaml
) -> None:
    """Even with every gate satisfied, _submit_order refuses."""
    settings_path = tmp_project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["trading"]["enabled"] = True
    settings["trading"]["dry_run_default"] = False
    settings["trading"]["require_manual_confirmation"] = False
    write_yaml(settings_path, settings)
    cfg = load_config(project_root=tmp_project)

    broker = Broker(cfg, client=MagicMock())
    with pytest.raises(TradingDisabled):
        broker.place_order(_make_intent(), confirmed=True, dry_run=False)


def test_cli_portfolio_does_not_call_place_order(monkeypatch, tmp_project: Path) -> None:
    """`bot.cli portfolio` must not invoke any order-placement path."""
    from bot import cli as cli_module
    from bot.ibkr_client import IBKRClient

    # Stub the connect method so we never touch a real socket.
    fake_summary = []
    fake_positions = []

    def fake_connect(self, timeout: float = 10.0) -> None:
        self._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(IBKRClient, "get_account_summary", lambda self, account=None: fake_summary)
    monkeypatch.setattr(IBKRClient, "get_positions", lambda self: fake_positions)
    monkeypatch.setattr(cli_module, "load_config", lambda: load_config(project_root=tmp_project))

    sentinel = MagicMock(side_effect=AssertionError("place_order must not be called"))
    monkeypatch.setattr("bot.broker.Broker.place_order", sentinel)

    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["portfolio"])
    assert result.exit_code == 0, result.stdout
    sentinel.assert_not_called()
