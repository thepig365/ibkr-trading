"""Live TWS port guard tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.config import load_config
from backend.connection.connection_manager import ConnectionManager, ConnectionState


@pytest.mark.asyncio
async def test_live_port_7496_blocked_by_default(monkeypatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("IBKR_ACCOUNT", "DU1")
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.example.yaml")
    cfg = cfg.model_copy(
        update={"ibkr": cfg.ibkr.model_copy(update={"port": 7496})}
    )
    mock_ib = MagicMock()
    mock_ib.isConnected.return_value = False
    cm = ConnectionManager(cfg, ib=mock_ib)
    await cm.connect()
    assert cm.state == ConnectionState.ERROR
    mock_ib.connectAsync.assert_not_called()
