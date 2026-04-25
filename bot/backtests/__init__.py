"""Backtest engine for ICT/SMC Intraday Liquidity Reversal V1 (Prompt 13E).

Pure-research package. NO order placement. NO live trading. NO broker
imports at module load. The IBKR client is touched only by the
``fetch-candles`` CLI command (see :mod:`bot.cli`); the engine itself
runs entirely from cached CSVs under ``data/candles/``.

Submodules
----------
* :mod:`.candle_cache`     — normalised CSV reader/writer.
* :mod:`.intraday_engine`  — no-lookahead day-trade simulator for
  ``ict_smc_intraday_v1`` (5m setup + 1m trigger).
* :mod:`.metrics`          — win-rate / R / drawdown / breakdowns.
* :mod:`.reports`          — JSON summary, CSV trades/equity, Markdown
  report and matplotlib charts.

Hard invariants
---------------
* ``execution_allowed`` is hard-coded ``False`` on every payload that
  leaves this package.
* ``paper_only`` is hard-coded ``True`` on every payload.
* No call site here imports :mod:`bot.broker` or
  :mod:`bot.ibkr_client`. Charts are matplotlib only and use a
  non-interactive backend.
"""

from __future__ import annotations

from .candle_cache import (
    BarRow,
    CandleCacheError,
    CandleCacheStats,
    cache_dir_for,
    load_candles,
    read_candles_csv,
    save_candles_csv,
    write_csv_for_day,
)
from .intraday_engine import (
    BACKTEST_STRATEGY_KEY,
    BacktestConfig,
    BacktestRun,
    Trade,
    backtest_intraday_smc,
    resample_bars,
)
from .metrics import (
    BacktestMetrics,
    SymbolBreakdown,
    compute_metrics,
)
from .reports import (
    REPORT_DIRNAME,
    save_backtest_artifacts,
)

__all__ = [
    "BACKTEST_STRATEGY_KEY",
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestRun",
    "BarRow",
    "CandleCacheError",
    "CandleCacheStats",
    "REPORT_DIRNAME",
    "SymbolBreakdown",
    "Trade",
    "backtest_intraday_smc",
    "cache_dir_for",
    "compute_metrics",
    "load_candles",
    "read_candles_csv",
    "resample_bars",
    "save_backtest_artifacts",
    "save_candles_csv",
    "write_csv_for_day",
]
