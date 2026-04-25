"""Strategy Registry + Multi-Strategy Engine (Prompt 13C).

Public API at a glance::

    from bot.strategies import (
        Strategy, StrategyContext, StrategyMetadata,
        StrategyScanResult, StrategySignal,
        StrategyRegistry, default_registry,
        StrategyRuntimeConfig, load_strategies_config,
        MultiStrategyEngine, MultiStrategyRunSummary,
        write_run_summary, write_single_scan,
    )

Hard rules — see ``bot/strategies/base.py`` and
``bot/strategies/registry.py`` for the rationale and tests:

* This package MUST NOT import :mod:`bot.broker`,
  :mod:`bot.ibkr_client`, or :mod:`ib_async` at module load. The
  ``mtf_smc`` adapter defers those imports to inside ``Strategy.scan``.
* Stub strategies (ict_smc_intraday_v1, chanlun_intraday_v1,
  orb_baseline) return ``status="not_implemented"`` and do nothing.
* The engine NEVER places orders. Wiring paper execution is a separate
  later phase.
"""

from __future__ import annotations

from .base import (
    ALLOWED_CONFIDENCES,
    ALLOWED_DIRECTIONS,
    ALLOWED_HORIZONS,
    ALLOWED_SCAN_STATUSES,
    ALLOWED_STRATEGY_STATUSES,
    Strategy,
    StrategyContext,
    StrategyMetadata,
    StrategyScanResult,
    StrategySignal,
)
from .config import (
    StrategyDefaults,
    StrategyEntryConfig,
    StrategyRuntimeConfig,
    load_strategies_config,
)
from .engine import (
    MultiStrategyEngine,
    MultiStrategyRunSummary,
    render_summary_zh,
    write_run_summary,
    write_single_scan,
)
from .registry import (
    StrategyRegistry,
    default_registry,
    iter_metadata,
    register_builtin_strategies,
    reset_default_registry_for_tests,
)

__all__ = [
    "ALLOWED_CONFIDENCES",
    "ALLOWED_DIRECTIONS",
    "ALLOWED_HORIZONS",
    "ALLOWED_SCAN_STATUSES",
    "ALLOWED_STRATEGY_STATUSES",
    "MultiStrategyEngine",
    "MultiStrategyRunSummary",
    "Strategy",
    "StrategyContext",
    "StrategyDefaults",
    "StrategyEntryConfig",
    "StrategyMetadata",
    "StrategyRegistry",
    "StrategyRuntimeConfig",
    "StrategyScanResult",
    "StrategySignal",
    "default_registry",
    "iter_metadata",
    "load_strategies_config",
    "register_builtin_strategies",
    "render_summary_zh",
    "reset_default_registry_for_tests",
    "write_run_summary",
    "write_single_scan",
]
