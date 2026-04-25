"""Unit tests for the intraday backtest engine (Prompt 13E PART B).

These tests focus on the engine's *trading-mechanics* invariants and on
``compute_metrics`` — they do not depend on the strategy detector
returning a signal (which is exercised separately in
``tests/test_ict_smc_intraday.py``). To keep the suite fast and
hermetic, we drive the simulation primitives directly with synthetic
:class:`BarRow` data and pre-built :class:`Trade` objects.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from bot.backtests import (
    BACKTEST_STRATEGY_KEY,
    BacktestConfig,
    Trade,
    backtest_intraday_smc,
    compute_metrics,
    resample_bars,
    save_backtest_artifacts,
    save_candles_csv,
)
from bot.backtests.candle_cache import BarRow
from bot.backtests.intraday_engine import (
    DIRECTION_LONG_ONLY,
    EOD_FORCE_FLAT_TIME,
    MODE_BOTH,
    MODE_STRICT_ONLY,
    _OpenPosition,
    _PendingOrder,
    _bar_at_or_after_eod,
    _close_eod,
    _signal_passes_filters,
    _step_position,
    _try_fill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bar(ts: str, *, o: float, h: float, l: float, c: float, v: float = 1000.0) -> BarRow:  # noqa: E741
    return BarRow(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _ts(day: str, h: int, m: int) -> str:
    return f"{day} {h:02d}:{m:02d}:00-04:00"


# ---------------------------------------------------------------------------
# resample_bars: no-lookahead aggregation
# ---------------------------------------------------------------------------
def test_resample_5m_only_emits_complete_windows() -> None:
    day = "2026-04-22"
    bars_1m = [
        _bar(_ts(day, 9, 30), o=100, h=101, l=99.5, c=100.5),
        _bar(_ts(day, 9, 31), o=100.5, h=101.2, l=100.0, c=101.0),
        _bar(_ts(day, 9, 32), o=101.0, h=101.3, l=100.8, c=101.1),
        _bar(_ts(day, 9, 33), o=101.1, h=101.5, l=100.9, c=101.4),
        _bar(_ts(day, 9, 34), o=101.4, h=101.6, l=101.2, c=101.5),
        # First 5m bar (09:30..09:34) is now complete.
        _bar(_ts(day, 9, 35), o=101.5, h=101.7, l=101.3, c=101.6),
        _bar(_ts(day, 9, 36), o=101.6, h=101.8, l=101.5, c=101.7),
    ]
    out = resample_bars(bars_1m, 5)
    # Expect at least one fully completed 5-minute window starting 09:30.
    assert out, "expected at least one completed 5m window"
    first = out[0]
    assert first["timestamp"].startswith("2026-04-22 09:30")
    assert first["high"] == max(b.high for b in bars_1m[:5])
    assert first["low"] == min(b.low for b in bars_1m[:5])
    assert first["close"] == bars_1m[4].close


def test_resample_30m_requires_full_window_count() -> None:
    """A partial 30m bar (only 5 underlying 1m bars) is NOT emitted."""
    day = "2026-04-22"
    bars_1m = [
        _bar(_ts(day, 9, 30 + i), o=100, h=100.5, l=99.5, c=100) for i in range(10)
    ]
    out = resample_bars(bars_1m, 30)
    # 10 < 30, so no complete 30m bar should be emitted (no-lookahead).
    assert out == []


# ---------------------------------------------------------------------------
# _try_fill: limit bracket fills only when bar trades through entry
# ---------------------------------------------------------------------------
def test_try_fill_long_fills_when_bar_trades_through_entry() -> None:
    p = _PendingOrder(
        direction="long", entry=100.0, stop=99.0, target=102.0,
        signal_category="DAY_TRADE_READY_STRICT", setup_type="", trigger_type="",
    )
    bar = _bar(_ts("2026-04-22", 10, 0), o=101.0, h=101.5, l=99.8, c=100.5)
    assert _try_fill(p, bar) is True


def test_try_fill_long_does_not_fill_when_bar_misses_entry() -> None:
    p = _PendingOrder(
        direction="long", entry=100.0, stop=99.0, target=102.0,
        signal_category="DAY_TRADE_READY_STRICT", setup_type="", trigger_type="",
    )
    bar = _bar(_ts("2026-04-22", 10, 0), o=101.0, h=101.5, l=100.5, c=101.2)
    assert _try_fill(p, bar) is None


# ---------------------------------------------------------------------------
# _step_position: stop / target / stop-first-on-tie
# ---------------------------------------------------------------------------
def _make_position(direction: str = "long", *, entry=100.0, stop=99.0, target=102.0) -> _OpenPosition:
    return _OpenPosition(
        direction=direction, entry=entry, stop=stop, target=target,
        signal_category="DAY_TRADE_READY_STRICT", setup_type="", trigger_type="",
        entered_at_dt=None, entered_at_ts=_ts("2026-04-22", 10, 0),
    )


def _bt_cfg() -> BacktestConfig:
    return BacktestConfig(
        symbols=("CRM",),
        start="2026-04-22",
        end="2026-04-22",
    )


def test_step_position_stop_hit_records_loss_minus_one_r() -> None:
    pos = _make_position()
    trades: list[Trade] = []
    bar = _bar(_ts("2026-04-22", 10, 5), o=99.5, h=99.8, l=98.5, c=98.7)
    _step_position(pos, bar, datetime(2026, 4, 22, 10, 6), trades, "CRM", "2026-04-22", _bt_cfg())
    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == "loss"
    assert t.pnl_r == pytest.approx(-1.0, rel=1e-6)


def test_step_position_target_hit_records_win_planned_r() -> None:
    pos = _make_position()  # entry 100, stop 99, target 102 → +2R
    trades: list[Trade] = []
    bar = _bar(_ts("2026-04-22", 10, 5), o=100.5, h=102.5, l=100.2, c=102.1)
    _step_position(pos, bar, datetime(2026, 4, 22, 10, 6), trades, "CRM", "2026-04-22", _bt_cfg())
    assert trades and trades[0].outcome == "win"
    assert trades[0].pnl_r == pytest.approx(2.0, rel=1e-6)


def test_step_position_simultaneous_stop_and_target_uses_stop_first() -> None:
    pos = _make_position()  # entry 100, stop 99, target 102
    trades: list[Trade] = []
    # Bar engulfs both stop and target.
    bar = _bar(_ts("2026-04-22", 10, 5), o=100.0, h=102.5, l=98.5, c=101.5)
    _step_position(pos, bar, datetime(2026, 4, 22, 10, 6), trades, "CRM", "2026-04-22", _bt_cfg())
    assert trades and trades[0].outcome == "loss"
    assert trades[0].pnl_r == pytest.approx(-1.0, rel=1e-6)


def test_step_position_short_target_records_planned_r() -> None:
    pos = _make_position(direction="short", entry=100.0, stop=101.0, target=98.0)  # +2R short
    trades: list[Trade] = []
    bar = _bar(_ts("2026-04-22", 10, 5), o=99.5, h=99.8, l=97.8, c=98.0)
    _step_position(pos, bar, datetime(2026, 4, 22, 10, 6), trades, "CRM", "2026-04-22", _bt_cfg())
    assert trades and trades[0].outcome == "win"
    assert trades[0].pnl_r == pytest.approx(2.0, rel=1e-6)


def test_close_eod_records_eod_exit_outcome() -> None:
    pos = _make_position()
    trades: list[Trade] = []
    bar = _bar(_ts("2026-04-22", 15, 55), o=100.5, h=100.6, l=100.3, c=100.4)
    _close_eod(pos, bar, datetime(2026, 4, 22, 15, 56), trades, "CRM", "2026-04-22")
    assert trades and trades[0].outcome == "eod_exit"
    assert trades[0].notes and "EOD" in trades[0].notes[0]


def test_bar_at_or_after_eod_uses_15_55_et_threshold() -> None:
    assert _bar_at_or_after_eod(datetime(2026, 4, 22, 15, 55))
    assert _bar_at_or_after_eod(datetime(2026, 4, 22, 16, 0))
    assert not _bar_at_or_after_eod(datetime(2026, 4, 22, 15, 30))
    assert EOD_FORCE_FLAT_TIME.hour == 15 and EOD_FORCE_FLAT_TIME.minute == 55


# ---------------------------------------------------------------------------
# Signal filter (mode / direction)
# ---------------------------------------------------------------------------
def test_signal_filter_strict_only_rejects_aggressive() -> None:
    cfg = BacktestConfig(symbols=("CRM",), start="2026-04-22", end="2026-04-22", mode=MODE_STRICT_ONLY)
    assert _signal_passes_filters("DAY_TRADE_READY_STRICT", "long", cfg)
    assert not _signal_passes_filters("DAY_TRADE_READY_AGGRESSIVE", "long", cfg)


def test_signal_filter_long_only_rejects_short() -> None:
    cfg = BacktestConfig(symbols=("CRM",), start="2026-04-22", end="2026-04-22", direction=DIRECTION_LONG_ONLY)
    assert _signal_passes_filters("DAY_TRADE_READY_STRICT", "long", cfg)
    assert not _signal_passes_filters("DAY_TRADE_READY_STRICT", "short", cfg)


def test_signal_filter_drops_non_actionable_categories() -> None:
    cfg = BacktestConfig(symbols=("CRM",), start="2026-04-22", end="2026-04-22", mode=MODE_BOTH)
    assert not _signal_passes_filters("WATCH_ONLY", "long", cfg)
    assert not _signal_passes_filters("BLOCKED", "long", cfg)
    assert not _signal_passes_filters("NO_SETUP", "long", cfg)


# ---------------------------------------------------------------------------
# BacktestConfig invariants
# ---------------------------------------------------------------------------
def test_backtest_config_rejects_execution_allowed_true() -> None:
    with pytest.raises(ValueError):
        BacktestConfig(symbols=("CRM",), start="2026-04-01", end="2026-04-02", execution_allowed=True)


def test_backtest_config_rejects_paper_only_false() -> None:
    with pytest.raises(ValueError):
        BacktestConfig(symbols=("CRM",), start="2026-04-01", end="2026-04-02", paper_only=False)


def test_backtest_config_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        BacktestConfig(symbols=("CRM",), start="2026-04-01", end="2026-04-02", mode="yolo")


# ---------------------------------------------------------------------------
# compute_metrics: end-to-end on hand-crafted trades
# ---------------------------------------------------------------------------
def _make_trade(
    *,
    symbol: str,
    pnl_r: float,
    outcome: str,
    direction: str = "long",
    category: str = "DAY_TRADE_READY_STRICT",
    entry_time: str = "2026-04-22 10:00:00-04:00",
    bars_held: int = 5,
) -> Trade:
    return Trade(
        trade_id=f"t-{symbol}-{pnl_r:+.1f}",
        symbol=symbol,
        date=entry_time[:10],
        strategy_id=BACKTEST_STRATEGY_KEY,
        direction=direction,
        signal_category=category,
        entry_time=entry_time,
        entry_price=100.0,
        stop_price=99.0,
        target_price=102.0,
        exit_time=entry_time,
        exit_price=100.0 + pnl_r,
        outcome=outcome,
        pnl_r=pnl_r,
        gross_pnl=pnl_r,
        planned_rr=2.0,
        bars_held=bars_held,
    )


def test_compute_metrics_summary_fields_and_drawdown() -> None:
    trades = [
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win"),
        _make_trade(symbol="CRM", pnl_r=-1.0, outcome="loss"),
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win"),
        _make_trade(symbol="CRM", pnl_r=-1.0, outcome="loss"),
        _make_trade(symbol="CRM", pnl_r=0.0, outcome="not_filled"),
    ]
    m = compute_metrics(trades, total_signals=5)
    assert m.total_signals == 5
    assert m.total_filled_trades == 4
    assert m.total_not_filled == 1
    assert m.win_rate == pytest.approx(0.5)
    assert m.total_r == pytest.approx(2.0)
    assert m.average_r == pytest.approx(0.5)
    assert m.max_drawdown_r <= 0  # peak was 2 → 1 → 3 → 2; max DD ≤ 0


def test_compute_metrics_strict_vs_aggressive_separate() -> None:
    trades = [
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win", category="DAY_TRADE_READY_STRICT"),
        _make_trade(symbol="CRM", pnl_r=-1.0, outcome="loss", category="DAY_TRADE_READY_STRICT"),
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win", category="DAY_TRADE_READY_AGGRESSIVE"),
    ]
    m = compute_metrics(trades, total_signals=3)
    assert m.strict_count == 2
    assert m.aggressive_count == 1
    assert m.strict_win_rate == pytest.approx(0.5)
    assert m.aggressive_win_rate == pytest.approx(1.0)


def test_compute_metrics_by_symbol_breakdown() -> None:
    trades = [
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win"),
        _make_trade(symbol="CRM", pnl_r=-1.0, outcome="loss"),
        _make_trade(symbol="AMZN", pnl_r=2.0, outcome="win"),
    ]
    m = compute_metrics(trades, total_signals=3)
    by_sym = {row.symbol: row for row in m.by_symbol}
    assert set(by_sym) == {"CRM", "AMZN"}
    assert by_sym["CRM"].trades == 2
    assert by_sym["CRM"].wins == 1
    assert by_sym["CRM"].losses == 1
    assert by_sym["AMZN"].win_rate == pytest.approx(1.0)


def test_compute_metrics_by_hour_breakdown() -> None:
    trades = [
        _make_trade(symbol="CRM", pnl_r=2.0, outcome="win", entry_time="2026-04-22 10:00:00-04:00"),
        _make_trade(symbol="CRM", pnl_r=-1.0, outcome="loss", entry_time="2026-04-22 10:30:00-04:00"),
        _make_trade(symbol="CRM", pnl_r=1.5, outcome="win", entry_time="2026-04-22 13:00:00-04:00"),
    ]
    m = compute_metrics(trades, total_signals=3)
    assert "10:00" in m.by_hour
    assert m.by_hour["10:00"]["trades"] == 2
    assert m.by_hour["10:00"]["wins"] == 1
    assert m.by_hour["13:00"]["trades"] == 1


# ---------------------------------------------------------------------------
# backtest_intraday_smc end-to-end (with empty cache)
# ---------------------------------------------------------------------------
def test_backtest_returns_empty_run_when_cache_missing(tmp_path: Path) -> None:
    cfg = BacktestConfig(
        symbols=("CRM",),
        start="2026-04-22",
        end="2026-04-23",
    )
    run = backtest_intraday_smc(tmp_path, cfg)
    assert run.trades == []
    assert run.metrics.total_filled_trades == 0
    assert run.paper_only is True
    assert run.execution_allowed is False
    assert any("no cached" in n for n in run.notes), run.notes


def test_backtest_runs_on_csv_fixture_without_calling_broker(tmp_path: Path) -> None:
    """Smoke-test: feed the engine real cached CSVs.

    The synthetic price series isn't engineered to produce ICT setups
    (those need 100+ 5m bars with specific liquidity sweeps), so we
    only assert the engine completes without error. Module-level
    isolation from ``bot.broker`` / ``bot.ibkr_client`` is verified in
    a separate subprocess test below — mutating ``sys.modules`` here
    would leak into other in-process tests that monkey-patch the
    IBKR client class.
    """
    day = "2026-04-22"
    bars = []
    price = 100.0
    for i in range(0, 200):
        h = 9 + ((30 + i) // 60)
        m = (30 + i) % 60
        if h >= 16:
            break
        bars.append({
            "timestamp": _ts(day, h, m),
            "open": price,
            "high": price + 0.3,
            "low": price - 0.3,
            "close": price + 0.05,
            "volume": 1_000.0,
        })
        price += 0.02
    save_candles_csv(tmp_path, "CRM", "1min", bars)

    run = backtest_intraday_smc(
        tmp_path,
        BacktestConfig(symbols=("CRM",), start=day, end=day),
    )
    assert run.paper_only is True
    assert run.execution_allowed is False
    assert all(t.outcome in {"win", "loss", "eod_exit", "not_filled"} for t in run.trades)


# ---------------------------------------------------------------------------
# save_backtest_artifacts: writes summary / trades / equity / report
# ---------------------------------------------------------------------------
def test_save_backtest_artifacts_writes_all_files(tmp_path: Path) -> None:
    cfg = BacktestConfig(symbols=("CRM",), start="2026-04-22", end="2026-04-22")
    run = backtest_intraday_smc(tmp_path, cfg)
    paths = save_backtest_artifacts(tmp_path, run, chart=False)
    for k in ("summary_json", "trades_csv", "equity_csv", "report_md"):
        assert k in paths
        assert Path(paths[k]).exists()
    summary = Path(paths["summary_json"]).read_text(encoding="utf-8")
    assert '"paper_only": true' in summary
    assert '"execution_allowed": false' in summary


# ---------------------------------------------------------------------------
# Engine module isolation
# ---------------------------------------------------------------------------
def test_backtests_package_does_not_import_broker_or_ibkr() -> None:
    """Importing the engine must NOT pull in the broker / IBKR client.

    Runs inside a fresh subprocess so we don't pollute the in-process
    ``sys.modules`` (other tests monkey-patch
    :class:`bot.ibkr_client.IBKRClient` and would break if we
    pop it here).
    """
    import json
    import subprocess

    code = (
        "import sys\n"
        "import bot.backtests  # noqa: F401\n"
        "import bot.backtests.intraday_engine  # noqa: F401\n"
        "import bot.backtests.metrics  # noqa: F401\n"
        "import bot.backtests.reports  # noqa: F401\n"
        "loaded = sorted(m for m in sys.modules if m in {'bot.broker', 'bot.ibkr_client'} or m.startswith('ib_async') or m.startswith('ib_insync'))\n"
        "import json; print(json.dumps(loaded))\n"
    )
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip())
    assert loaded == [], (
        f"bot.backtests pulled in broker-related modules: {loaded}. "
        "The engine must stay decoupled from the broker stack."
    )
