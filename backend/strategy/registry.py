"""Strategy registry - one-line strategy selection from config.yaml."""

from __future__ import annotations

import logging
from typing import Type

from backend.config import AppConfig
from backend.strategy.base import BaseStrategy
from backend.strategy.ict_strategy import ICTStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Map ``config.strategy`` -> a BaseStrategy subclass."""

    _strategies: dict[str, Type[BaseStrategy]] = {
        "ICT": ICTStrategy,
    }

    @classmethod
    def register(cls, name: str, strategy_cls: Type[BaseStrategy]) -> None:
        """Register a new strategy implementation under a config name."""

        cls._strategies[name] = strategy_cls

    @classmethod
    def available(cls) -> list[str]:
        """Return the sorted list of registered strategy names."""

        return sorted(cls._strategies.keys())

    @classmethod
    def load(cls, config: AppConfig) -> BaseStrategy:
        """Instantiate the strategy named in ``config.strategy``."""

        name = config.strategy
        if name not in cls._strategies:
            raise ValueError(
                f"Unknown strategy '{name}'. Available: {cls.available()}"
            )
        logger.info("Loaded strategy: %s", name)
        return cls._strategies[name](config)
