"""Strategy Registry — single source of truth for strategy keys + objects.

The registry is a tiny, deterministic, in-process dict:

* :py:meth:`StrategyRegistry.register` — adds a strategy. Duplicate
  keys raise loudly (so a refactor cannot silently shadow an existing
  strategy).
* :py:meth:`StrategyRegistry.get` — fetches by key. Missing keys raise
  :class:`KeyError` with a clear message.
* :py:meth:`StrategyRegistry.list_metadata` — returns metadata for all
  registered strategies, in the deterministic insertion order.

A module-level ``default_registry()`` lazy-builds the canonical registry
shared by the CLI / engine / state store. ``register_builtin_strategies()``
is idempotent: calling it twice is a no-op.

Module hygiene rules (enforced by tests):

* Importing this module MUST NOT import :mod:`bot.broker`,
  :mod:`bot.ibkr_client`, or :mod:`ib_async`. Adapter modules are
  imported lazily on first registry build.
* Adapter modules themselves MUST NOT import broker code at module
  load — only inside ``Strategy.scan``.

This separation lets the FastAPI UI safely read strategy metadata on
page render without any TWS connection.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

from .base import Strategy, StrategyMetadata

LOG = logging.getLogger(__name__)


class StrategyRegistry:
    """Mutable, ordered registry of :class:`Strategy` instances."""

    def __init__(self) -> None:
        self._items: dict[str, Strategy] = {}

    # ------------------------------------------------------------------
    def register(self, strategy: Strategy, *, replace: bool = False) -> None:
        meta = getattr(strategy, "metadata", None)
        if not isinstance(meta, StrategyMetadata):
            raise TypeError(
                "Strategy must expose a StrategyMetadata 'metadata' attribute."
            )
        key = meta.key
        if not key:
            raise ValueError("Strategy metadata.key cannot be empty.")
        if key in self._items and not replace:
            raise ValueError(
                f"Strategy key {key!r} already registered. "
                "Pass replace=True only in tests."
            )
        self._items[key] = strategy

    # ------------------------------------------------------------------
    def get(self, key: str) -> Strategy:
        try:
            return self._items[key]
        except KeyError as exc:
            raise KeyError(
                f"Strategy {key!r} is not registered. Known keys: {sorted(self._items)}"
            ) from exc

    # ------------------------------------------------------------------
    def has(self, key: str) -> bool:
        return key in self._items

    # ------------------------------------------------------------------
    def keys(self) -> list[str]:
        return list(self._items.keys())

    # ------------------------------------------------------------------
    def list_strategies(self) -> list[Strategy]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    def list_metadata(self) -> list[StrategyMetadata]:
        return [s.metadata for s in self._items.values()]

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Drop every registration. For test isolation only."""
        self._items.clear()


# ---------------------------------------------------------------------------
# Module-level default registry
# ---------------------------------------------------------------------------

_default_registry: StrategyRegistry | None = None
_default_lock = threading.Lock()


def default_registry() -> StrategyRegistry:
    """Return the process-wide default registry (lazy + thread-safe)."""
    global _default_registry
    if _default_registry is not None:
        return _default_registry
    with _default_lock:
        if _default_registry is None:
            reg = StrategyRegistry()
            register_builtin_strategies(reg)
            _default_registry = reg
    return _default_registry


def reset_default_registry_for_tests() -> None:
    """Force the next ``default_registry()`` call to rebuild from scratch."""
    global _default_registry
    with _default_lock:
        _default_registry = None


def register_builtin_strategies(registry: StrategyRegistry) -> None:
    """Register the canonical set of strategies for this project.

    Intentionally lazy-imports each adapter module so a typo or heavy
    import in one adapter cannot bring down the whole UI / CLI startup.
    Each adapter module MUST NOT touch the broker at import time —
    that's enforced by ``tests/test_strategy_registry.py``.
    """
    # mtf_smc — wraps the existing MTF SMC/ICT swing scanner. Status: ready.
    try:
        from .adapters.mtf_smc_adapter import MtfSmcStrategy

        registry.register(MtfSmcStrategy(), replace=True)
    except Exception as exc:  # noqa: BLE001 - never crash registry build
        LOG.warning("Skipping mtf_smc registration: %s", exc)

    # ict_smc_intraday_v1 — placeholder for the upcoming intraday SMC/ICT.
    try:
        from .adapters.ict_smc_intraday_v1 import IctSmcIntradayV1Strategy

        registry.register(IctSmcIntradayV1Strategy(), replace=True)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Skipping ict_smc_intraday_v1 registration: %s", exc)

    # chanlun_intraday_v1 — placeholder for the upcoming Chanlun intraday.
    try:
        from .adapters.chanlun_intraday_v1 import ChanlunIntradayV1Strategy

        registry.register(ChanlunIntradayV1Strategy(), replace=True)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Skipping chanlun_intraday_v1 registration: %s", exc)

    # orb_baseline — placeholder for an opening-range-breakout baseline.
    try:
        from .adapters.orb_baseline import OrbBaselineStrategy

        registry.register(OrbBaselineStrategy(), replace=True)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Skipping orb_baseline registration: %s", exc)


def iter_metadata(registry: StrategyRegistry | None = None) -> Iterable[StrategyMetadata]:
    """Convenience iterator used by CLI listings and the UI."""
    reg = registry or default_registry()
    yield from reg.list_metadata()


__all__ = [
    "StrategyRegistry",
    "default_registry",
    "register_builtin_strategies",
    "reset_default_registry_for_tests",
    "iter_metadata",
]
