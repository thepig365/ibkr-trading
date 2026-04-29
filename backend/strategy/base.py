"""Abstract base class for all pluggable trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from backend.strategy.models import Bar, Signal


class BaseStrategy(ABC):
    """All strategies implement this interface; the engine never reads internals.

    The engine drives a strategy through three calls:

    * :meth:`on_bar` — once per completed bar, returns a Signal or None.
    * :meth:`on_position_update` — when an open position price updates.
    * :meth:`get_required_timeframes` — declared at startup so the data layer
      knows what to subscribe.

    :meth:`is_active_time` lets a strategy gate its own time windows; the
    engine may still call ``on_bar`` outside that window for historical
    persistence, but the strategy must return ``None`` when inactive.
    """

    name: str = "Base"

    def __init__(self, config: Any) -> None:
        self.config = config

    @abstractmethod
    def on_bar(self, bar: Bar, context: dict[str, Any]) -> Optional[Signal]:
        """Called on every completed 1m bar. Return Signal or None."""

    @abstractmethod
    def on_position_update(
        self, position: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Called when an open position price updates. Optional dynamic moves."""

    @abstractmethod
    def get_required_timeframes(self) -> list[str]:
        """Return timeframes the engine should subscribe (e.g. ['1m','15m'])."""

    def is_active_time(self, now_ny: datetime) -> bool:
        """Return True when the strategy is willing to trade at the given NY time."""

        return True
