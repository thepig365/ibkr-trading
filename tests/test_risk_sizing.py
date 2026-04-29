"""Risk manager sizing + circuit-breaker tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import pytz

from backend.config import load_config
from backend.db.database import Database
from backend.execution.risk_manager import RiskManager
from backend.strategy.models import Direction, Signal

NY = pytz.timezone("America/New_York")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.example.yaml"


def _signal(entry: float, stop: float, take: float) -> Signal:
    return Signal(
        strategy_name="ICT_1m",
        symbol="SPY",
        direction=Direction.LONG,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take,
        confidence=0.8,
        reason="test",
        timeframe="1m",
        timestamp=NY.localize(datetime(2026, 4, 29, 10, 30)),
        score=70,
        auto_execute=True,
    )


@pytest.mark.asyncio
async def test_size_signal_within_caps(tmp_path) -> None:
    cfg = load_config(CONFIG_PATH)
    db = Database(tmp_path / "trades.db")
    await db.initialize()
    rm = RiskManager(cfg, db, starting_equity=100_000)

    signal = _signal(500.0, 498.0, 506.0)
    sizing = rm.size_signal(signal)
    assert sizing is not None
    # 1% of 100k = 1000 risk, /2 per share = 500 shares max
    # Notional cap 20k / 500 = 40 shares
    assert sizing.shares == 40
    assert sizing.notional == 20_000


@pytest.mark.asyncio
async def test_validate_rejects_low_rr(tmp_path) -> None:
    cfg = load_config(CONFIG_PATH)
    db = Database(tmp_path / "trades.db")
    await db.initialize()
    rm = RiskManager(cfg, db)
    signal = _signal(500.0, 499.0, 500.5)  # 0.5 R reward
    ok, reason = rm.validate_signal(signal)
    assert ok is False
    assert reason and reason.startswith("rr_below_min")


@pytest.mark.asyncio
async def test_circuit_breaker_triggers_at_2_pct_loss(tmp_path) -> None:
    cfg = load_config(CONFIG_PATH)
    db = Database(tmp_path / "trades.db")
    await db.initialize()
    rm = RiskManager(cfg, db, starting_equity=100_000)
    rm.register_trade_close(-2_500)
    assert rm.check_circuit_breaker() is True


@pytest.mark.asyncio
async def test_capital_cap_blocks_overage(tmp_path) -> None:
    cfg = load_config(CONFIG_PATH)
    db = Database(tmp_path / "trades.db")
    await db.initialize()
    rm = RiskManager(cfg, db)
    rm.consume_capital(95_000)
    assert rm.has_capital_capacity(10_000) is False
