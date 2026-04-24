"""Tests for `bot.reconciliation`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bot.broker import Broker
from bot.config import load_config
from bot.ibkr_client import OpenOrderRow, PositionRow
from bot.journal import Journal
from bot.reconciliation import reconcile


def _row_pos(symbol: str, qty: float = 100, account: str = "DU111") -> PositionRow:
    return PositionRow(
        account=account, symbol=symbol, sec_type="STK",
        exchange="ARCA", currency="USD", position=qty, avg_cost=100.0,
    )


def _row_order(
    symbol: str, perm_id: int, action: str = "SELL", order_type: str = "STP",
) -> OpenOrderRow:
    return OpenOrderRow(
        perm_id=perm_id, order_id=perm_id, account="DU111", symbol=symbol,
        sec_type="STK", action=action, order_type=order_type,
        total_quantity=100, lmt_price=None, aux_price=95.0, tif="DAY",
        status="Submitted",
    )


def _make_broker(positions, orders) -> Broker:
    broker = MagicMock(spec=Broker)
    broker.get_positions.return_value = positions
    broker.get_open_orders.return_value = orders
    return broker


def test_reconciliation_passes_when_every_position_has_a_stop(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    broker = _make_broker(
        positions=[_row_pos("AAPL", 100)],
        orders=[_row_order("AAPL", perm_id=1)],
    )
    journal.record_open_order(_row_order("AAPL", perm_id=1).to_dict(), source="test")
    journal.record_positions_snapshot([_row_pos("AAPL", 100).to_dict()])
    report = reconcile(broker, journal)
    assert report.passed is True


def test_reconciliation_flags_position_without_stop(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    broker = _make_broker(positions=[_row_pos("AAPL", 100)], orders=[])
    report = reconcile(broker, journal)
    assert report.passed is False
    assert "AAPL" in report.positions_without_stops


def test_reconciliation_flags_unknown_open_orders(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    broker = _make_broker(
        positions=[_row_pos("AAPL", 100)],
        orders=[_row_order("AAPL", perm_id=42)],
    )
    # Local journal knows nothing about perm_id=42.
    report = reconcile(broker, journal)
    assert any(o["perm_id"] == 42 for o in report.unknown_open_orders)
    assert report.passed is False


def test_reconciliation_flags_missing_local_records(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    journal.record_positions_snapshot([_row_pos("MSFT", 50).to_dict()])
    broker = _make_broker(positions=[], orders=[])
    report = reconcile(broker, journal)
    assert "MSFT" in report.missing_local_records
    assert report.passed is False


def test_reconciliation_never_calls_order_placement(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    broker = MagicMock(spec=Broker)
    broker.get_positions.return_value = []
    broker.get_open_orders.return_value = []
    reconcile(broker, journal)
    # Ensure none of the write paths were invoked.
    assert not broker.place_order.called
    # Defensive: even attribute access for non-existent write methods
    # should not produce calls.
    for forbidden in ("placeOrder", "submitOrder", "_submit_order"):
        assert not hasattr(broker, forbidden) or not getattr(broker, forbidden).called


def test_reconcile_failure_triggers_notification_fallback(
    monkeypatch, tmp_project: Path
) -> None:
    """When reconcile fails in the CLI, a notification (or fallback) must fire.

    Telegram credentials are not set in the test env, so the
    notification path falls through to memory/DAILY-SUMMARY.md. We
    also assert the reconciliation logic itself was not weakened: the
    flagged position still appears in the report.
    """
    from typer.testing import CliRunner

    from bot import cli as cli_module
    from bot.ibkr_client import IBKRClient

    def fake_connect(self, timeout: float = 10.0, *args, **kwargs) -> None:
        self._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(
        IBKRClient, "get_positions", lambda self: [_row_pos("AAPL", 100)]
    )
    monkeypatch.setattr(IBKRClient, "get_open_orders", lambda self: [])
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["reconcile"])
    assert result.exit_code == 3, result.stdout
    assert "FAIL" in result.stdout
    assert "AAPL" in result.stdout

    summary_path = tmp_project / "memory" / "DAILY-SUMMARY.md"
    assert summary_path.exists(), "reconcile FAIL must trigger fallback notification"
    summary = summary_path.read_text(encoding="utf-8")
    assert "Reconciliation Failed" in summary
    assert "trading remains blocked" in summary
