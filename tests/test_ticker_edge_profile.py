"""Tests for :mod:`bot.edge.ticker_edge` (Prompt 13L-alt)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.backtests.metrics import SIGNAL_STRICT
from bot.edge.reports import save_edge_profiles_artifacts
from bot.edge.ticker_edge import (
    REC_DISABLED,
    REC_STRICT_AND_AGGRESSIVE,
    REC_STRICT_ONLY,
    REC_WATCH_ONLY,
    _classify_and_score,
    build_ticker_edge_profile,
    edge_profile_insufficient,
)
from bot.edge.ticker_edge import (
    DEFAULT_MAX_DRAWDOWN_R_LIMIT as MDD_LIM,
)


def _trade_dict(
    i: int,
    *,
    pnl: float,
    category: str = SIGNAL_STRICT,
    direction: str = "long",
    outcome: str = "win",
    symbol: str = "TEST",
) -> dict:
    o = outcome if pnl == 0 else ("win" if pnl > 0 else "loss")
    return {
        "trade_id": f"t{i}",
        "symbol": symbol,
        "date": "2026-01-15",
        "strategy_id": "ict_smc_intraday_v1",
        "direction": direction,
        "signal_category": category,
        "setup_type": "x",
        "trigger_type": "y",
        "entry_time": "2026-01-15T10:00:00",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "target_price": 102.0,
        "exit_time": "2026-01-15T10:30:00",
        "outcome": o,
        "pnl_r": pnl,
        "gross_pnl": 0.0,
        "planned_rr": 1.2,
    }


def test_build_from_positive_backtest() -> None:
    trades = [
        _trade_dict(i, pnl=0.25, category=SIGNAL_STRICT)
        for i in range(20)
    ]
    sm = {
        "strategy_id": "ict_smc_intraday_v1",
        "config": {"start": "2026-01-01", "end": "2026-01-20"},
        "metrics": {
            "total_signals": 25,
            "average_r": 0.25,
            "profit_factor": 1.8,
            "max_drawdown_r": -1.0,
            "win_rate": 0.7,
            "total_r": 5.0,
        },
        "trades": trades,
    }
    p = build_ticker_edge_profile(
        "TEST",
        sm,
        min_trades_moderate=15,
        min_trades_strong=20,
    )
    assert p.symbol == "TEST"
    assert p.filled_trades == 20
    assert p.recommended_mode in (REC_STRICT_ONLY, REC_STRICT_AND_AGGRESSIVE)
    assert p.confidence_level in ("strong", "moderate")
    assert p.edge_score > 30


def test_build_from_negative_backtest() -> None:
    trades = [
        _trade_dict(
            i, pnl=-0.2, category=SIGNAL_STRICT, outcome="loss", symbol="X"
        )
        for i in range(18)
    ]
    sm = {
        "strategy_id": "ict_smc_intraday_v1",
        "config": {"start": "2026-01-01", "end": "2026-01-20"},
        "metrics": {
            "total_signals": 20,
            "average_r": -0.2,
            "profit_factor": 0.5,
            "max_drawdown_r": -5.0,
        },
        "trades": trades,
    }
    p = build_ticker_edge_profile("X", sm, min_trades_moderate=5, min_trades_strong=60)
    assert p.recommended_mode == REC_DISABLED
    assert p.max_risk_multiplier == 0.0


def test_insufficient_data_small_sample() -> None:
    trades = [_trade_dict(i, pnl=0.1) for i in range(3)]
    sm = {
        "config": {"start": "a", "end": "b"},
        "metrics": {"total_signals": 8, "average_r": 0.1, "profit_factor": 1.2},
        "trades": trades,
    }
    p = build_ticker_edge_profile("S", sm, min_trades_moderate=30)
    assert p.confidence_level == "insufficient_data"
    assert p.filled_trades < 20


def test_profit_factor_and_average_r_increase_score() -> None:
    a = _classify_and_score(
        n_filled=25,
        n_signals=30,
        avg_r=0.2,
        pf=1.8,
        mdd=0.0,
        min_mod=10,
        min_str=20,
        max_dd_limit=50.0,
    )
    b = _classify_and_score(
        n_filled=25,
        n_signals=30,
        avg_r=0.05,
        pf=1.1,
        mdd=0.0,
        min_mod=10,
        min_str=20,
        max_dd_limit=50.0,
    )
    assert a[0] > b[0]


def test_high_drawdown_penalizes_score() -> None:
    lo = _classify_and_score(
        n_filled=25,
        n_signals=30,
        avg_r=0.2,
        pf=1.6,
        mdd=-1.0,
        min_mod=10,
        min_str=20,
        max_dd_limit=MDD_LIM,
    )
    hi = _classify_and_score(
        n_filled=25,
        n_signals=30,
        avg_r=0.2,
        pf=1.6,
        mdd=-50.0,
        min_mod=10,
        min_str=20,
        max_dd_limit=MDD_LIM,
    )
    assert lo[0] > hi[0]


def test_recommended_modes() -> None:
    _es, _conf, rec, mrm, _ = _classify_and_score(
        n_filled=20,
        n_signals=30,
        avg_r=0.25,
        pf=2.0,
        mdd=-1.0,
        min_mod=5,
        min_str=20,
        max_dd_limit=30.0,
    )
    assert rec == REC_STRICT_AND_AGGRESSIVE
    assert mrm == 1.0

    _e2, _c2, rec2, mrm2, _ = _classify_and_score(
        n_filled=15,
        n_signals=20,
        avg_r=0.05,
        pf=1.1,
        mdd=-2.0,
        min_mod=10,
        min_str=60,
        max_dd_limit=30.0,
    )
    assert rec2 in (REC_STRICT_AND_AGGRESSIVE, REC_STRICT_ONLY, REC_WATCH_ONLY)
    assert 0.0 <= mrm2 <= 1.0


def test_cli_writes_profile_json(tmp_path: Path) -> None:
    p1 = edge_profile_insufficient("ZZ", "ict_smc_intraday_v1", "a", "b", "unit")
    paths = save_edge_profiles_artifacts(tmp_path, [p1], run_date="2026-01-20")
    jp = Path(paths["json"])
    assert jp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert "profiles" in data
    assert data["profiles"][0]["symbol"] == "ZZ"
    mp = Path(paths["md"])
    assert mp.is_file() and "edge" in mp.read_text(encoding="utf-8").lower()
