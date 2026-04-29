"""Forex ICT 1M paper test — utilities. Does not import stock intraday engine."""

from __future__ import annotations

FOREX_ORDER_REF_PREFIX_DEFAULT = "STRATEGYLAB_FX_ICT_1M"
FOREX_CANDLES_ROOT = "data/candles_forex"
FOREX_ORDERS_DIR = "data/forex_orders"
FOREX_RUNTIME_LAST = "data/runtime/forex_ict_1m_last.json"

__all__ = [
    "FOREX_ORDER_REF_PREFIX_DEFAULT",
    "FOREX_CANDLES_ROOT",
    "FOREX_ORDERS_DIR",
    "FOREX_RUNTIME_LAST",
]

# Submodules: forex.pairs, forex.runner, forex.fetch_bridge (avoid stock engine import here).
