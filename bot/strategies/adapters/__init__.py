"""Strategy adapters package.

Each module here exposes ONE class implementing the
:class:`bot.strategies.base.Strategy` Protocol.

Hard rules (enforced by tests/test_strategy_registry.py and
tests/test_strategy_mtf_adapter.py):

* No module in this package may import :mod:`bot.broker`,
  :mod:`bot.ibkr_client`, or :mod:`ib_async` at import time.
* Heavy imports (broker / IBKR / matplotlib) MUST be deferred until
  inside ``Strategy.scan``.
* Stub adapters (ict_smc_intraday_v1, chanlun_intraday_v1, orb_baseline)
  return ``StrategyScanResult(status="not_implemented", ...)`` without
  doing any work.
"""

from __future__ import annotations
