"""ICT/SMC Intraday Liquidity Reversal V1 strategy package.

Exposes the dataclasses, detection primitives, and scan orchestrators
used by:

* :mod:`bot.strategies.adapters.ict_smc_intraday_v1` (registry adapter),
* :mod:`bot.cli` (``scan-intraday-smc`` / ``scan-intraday-smc-watchlist``),
* :mod:`bot_ui.routes.signals` (read-only summary rendering).

This package NEVER imports :mod:`bot.broker` or any TWS-touching code at
module load. IBKR access happens lazily inside
:func:`scanner.scan_symbol_with_ibkr` /
:func:`scanner.scan_watchlist_with_ibkr` and only when explicitly
invoked from a CLI / worker scan.
"""

from __future__ import annotations

from .charts import render_intraday_charts
from .detector import (
    build_intraday_context,
    detect_1m_entry_trigger,
    detect_5m_setup,
)
from .model import (
    ALLOWED_SIGNAL_CATEGORIES,
    DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT,
    DEFAULT_MAX_STOP_DISTANCE_PCT,
    DEFAULT_MIN_RR_AGGRESSIVE,
    DEFAULT_MIN_RR_STRICT,
    DIRECTION_FLAT,
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_SOURCE_BREAKER,
    ENTRY_SOURCE_FVG,
    ENTRY_SOURCE_NONE,
    ENTRY_SOURCE_OB,
    FiveMinuteSetup,
    IntradayContext,
    IntradayEvaluation,
    IntradayRiskConfig,
    IntradayTradePlan,
    LiquidityLevel,
    OneMinuteTrigger,
    SIGNAL_BLOCKED,
    SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
    SIGNAL_DAY_TRADE_READY_STRICT,
    SIGNAL_ERROR,
    SIGNAL_INVALID_RISK,
    SIGNAL_NO_SETUP,
    SIGNAL_WATCH_ONLY,
    STRATEGY_KEY,
    STRATEGY_NAME,
    STRATEGY_VERSION,
)
from .scanner import (
    build_intraday_trade_plan,
    build_watchlist_summary,
    classify_intraday_signal,
    format_intraday_telegram_zh,
    save_intraday_evaluation,
    save_intraday_watchlist_summary,
    scan_symbol_from_bars,
    scan_symbol_with_ibkr,
    scan_watchlist_with_ibkr,
)

__all__ = [
    "ALLOWED_SIGNAL_CATEGORIES",
    "DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT",
    "DEFAULT_MAX_STOP_DISTANCE_PCT",
    "DEFAULT_MIN_RR_AGGRESSIVE",
    "DEFAULT_MIN_RR_STRICT",
    "DIRECTION_FLAT",
    "DIRECTION_LONG",
    "DIRECTION_SHORT",
    "ENTRY_SOURCE_BREAKER",
    "ENTRY_SOURCE_FVG",
    "ENTRY_SOURCE_NONE",
    "ENTRY_SOURCE_OB",
    "FiveMinuteSetup",
    "IntradayContext",
    "IntradayEvaluation",
    "IntradayRiskConfig",
    "IntradayTradePlan",
    "LiquidityLevel",
    "OneMinuteTrigger",
    "SIGNAL_BLOCKED",
    "SIGNAL_DAY_TRADE_READY_AGGRESSIVE",
    "SIGNAL_DAY_TRADE_READY_STRICT",
    "SIGNAL_ERROR",
    "SIGNAL_INVALID_RISK",
    "SIGNAL_NO_SETUP",
    "SIGNAL_WATCH_ONLY",
    "STRATEGY_KEY",
    "STRATEGY_NAME",
    "STRATEGY_VERSION",
    "build_intraday_context",
    "build_intraday_trade_plan",
    "build_watchlist_summary",
    "classify_intraday_signal",
    "detect_1m_entry_trigger",
    "detect_5m_setup",
    "format_intraday_telegram_zh",
    "render_intraday_charts",
    "save_intraday_evaluation",
    "save_intraday_watchlist_summary",
    "scan_symbol_from_bars",
    "scan_symbol_with_ibkr",
    "scan_watchlist_with_ibkr",
]
