"""Strategy base types: metadata, context, signal, scan result, Protocol.

This module is the foundation of the multi-strategy architecture. It is
intentionally pure-Python with NO broker / IBKR / network imports so it
can be safely imported by:

* the CLI (which then triggers worker-side scans via subprocess),
* the FastAPI UI (which only renders summaries from disk), and
* unit tests (which exercise the registry / engine without TWS).

Hard architectural rules (enforced by tests in
``tests/test_strategy_base.py`` and ``tests/test_ui_strategies_page``):

1. No strategy adapter may import :mod:`bot.broker`, :mod:`bot.ibkr_client`,
   or any TWS client at module load. Heavy imports MUST happen lazily
   inside :py:meth:`Strategy.scan`.
2. ``StrategyScanResult.execution_allowed`` is always ``False`` at this
   stage of the project. Paper execution is wired in a later phase.
3. ``StrategyContext.paper_only`` is an invariant — strategies must
   refuse to do anything write-side if a future caller flips it.

These types form the public API consumed by:

* :mod:`bot.strategies.registry`
* :mod:`bot.strategies.engine`
* :mod:`bot.strategies.adapters` (mtf_smc, ict_smc_intraday_v1,
  chanlun_intraday_v1, orb_baseline)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Enumerations (kept as simple Literal-style strings for JSON friendliness).
# ---------------------------------------------------------------------------

# horizon: "swing" | "intraday" | "scalp" | "research"
# status:  "ready" | "experimental" | "not_implemented" | "deprecated"
# scan status: "ok" | "skipped" | "not_implemented" | "error"
# direction:  "long" | "short" | "flat" | "unknown"
# confidence: "high" | "medium" | "low" | "unknown"

ALLOWED_HORIZONS: frozenset[str] = frozenset(
    {"swing", "intraday", "scalp", "research"}
)
ALLOWED_STRATEGY_STATUSES: frozenset[str] = frozenset(
    {"ready", "experimental", "not_implemented", "deprecated"}
)
ALLOWED_SCAN_STATUSES: frozenset[str] = frozenset(
    {"ok", "skipped", "not_implemented", "error"}
)
ALLOWED_DIRECTIONS: frozenset[str] = frozenset(
    {"long", "short", "flat", "unknown"}
)
ALLOWED_CONFIDENCES: frozenset[str] = frozenset(
    {"high", "medium", "low", "unknown"}
)


def _utc_now_iso() -> str:
    """Return UTC ISO-8601 timestamp with second precision (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyMetadata:
    """Static, declarative description of a strategy.

    ``key`` is the canonical identifier used by the registry and CLI.
    It MUST match ``^[a-z][a-z0-9_]{1,30}$`` and be globally unique
    among registered strategies.
    """

    key: str
    name: str
    version: str
    description_zh: str
    timeframes: tuple[str, ...]
    horizon: str
    research_only: bool = True
    requires_ibkr: bool = True
    enabled_by_default: bool = False
    status: str = "ready"
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon not in ALLOWED_HORIZONS:
            raise ValueError(
                f"StrategyMetadata.horizon={self.horizon!r} not in {sorted(ALLOWED_HORIZONS)}"
            )
        if self.status not in ALLOWED_STRATEGY_STATUSES:
            raise ValueError(
                f"StrategyMetadata.status={self.status!r} not in {sorted(ALLOWED_STRATEGY_STATUSES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "description_zh": self.description_zh,
            "timeframes": list(self.timeframes),
            "horizon": self.horizon,
            "research_only": self.research_only,
            "requires_ibkr": self.requires_ibkr,
            "enabled_by_default": self.enabled_by_default,
            "status": self.status,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class StrategySignal:
    """A single trading idea produced by a strategy scan.

    Signals are RESEARCH-ONLY at this stage. They are persisted to JSON
    for the UI / Telegram / future strategy-selection layer. They are
    NOT order tickets — there is no ``quantity``, no ``order_type``, no
    ``time_in_force``. Wiring execution is a separate, later phase.
    """

    strategy_key: str
    symbol: str
    direction: str
    confidence: str
    horizon: str
    timeframe: str
    timestamp_utc: str = field(default_factory=_utc_now_iso)
    score: float | None = None
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(
                f"StrategySignal.direction={self.direction!r} not in {sorted(ALLOWED_DIRECTIONS)}"
            )
        if self.confidence not in ALLOWED_CONFIDENCES:
            raise ValueError(
                f"StrategySignal.confidence={self.confidence!r} not in {sorted(ALLOWED_CONFIDENCES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_key": self.strategy_key,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "horizon": self.horizon,
            "timeframe": self.timeframe,
            "timestamp_utc": self.timestamp_utc,
            "score": self.score,
            "reason": self.reason,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class StrategyContext:
    """Inputs handed to a strategy by the engine on each scan.

    * ``cfg`` / ``journal`` are the existing :class:`bot.config.AppConfig`
      and :class:`bot.journal.Journal`. They are ``Any`` here only so
      this module stays free of broker imports.
    * ``symbols`` is the resolved priority watchlist for this scan.
    * ``paper_only`` is ALWAYS True at this stage. Strategies that flip
      to ``False`` later must guard against it themselves.
    * ``paper_execution_allowed`` is ALWAYS False at this stage. The
      Strategy Registry / Multi-Strategy Engine NEVER places orders;
      execution belongs in :mod:`bot.auto_paper_mtf` and similar.
    """

    cfg: Any = None
    journal: Any = None
    symbols: tuple[str, ...] = ()
    market_regime: str = "neutral"
    regime_confidence: str = "medium"
    paper_only: bool = True
    paper_execution_allowed: bool = False
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyScanResult:
    """Outcome of one strategy scan over a watchlist."""

    strategy_key: str
    started_utc: str
    finished_utc: str
    status: str
    symbol_count: int
    signals: list[StrategySignal] = field(default_factory=list)
    summary: Mapping[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    execution_allowed: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if self.status not in ALLOWED_SCAN_STATUSES:
            raise ValueError(
                f"StrategyScanResult.status={self.status!r} not in {sorted(ALLOWED_SCAN_STATUSES)}"
            )
        # paper-only invariant: at this stage of the project, NO scan
        # may ever announce that execution is allowed. Wiring paper
        # execution is a separate, explicit phase.
        if self.execution_allowed:
            raise ValueError(
                "StrategyScanResult.execution_allowed must be False at this stage of the project."
            )
        if not self.paper_only:
            raise ValueError(
                "StrategyScanResult.paper_only must be True (paper-only invariant)."
            )

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_key": self.strategy_key,
            "started_utc": self.started_utc,
            "finished_utc": self.finished_utc,
            "status": self.status,
            "symbol_count": self.symbol_count,
            "signal_count": self.signal_count,
            "signals": [s.to_dict() for s in self.signals],
            "summary": dict(self.summary),
            "notes": list(self.notes),
            "error": self.error,
            "execution_allowed": self.execution_allowed,
            "paper_only": self.paper_only,
        }


@runtime_checkable
class Strategy(Protocol):
    """Minimal Protocol every strategy adapter / stub must satisfy.

    ``metadata`` is a class- or instance-level :class:`StrategyMetadata`.
    ``scan(ctx)`` MUST return a :class:`StrategyScanResult` and MUST
    NOT call ``broker.place_order`` / ``broker.cancel_order`` / any
    other write API at this stage.
    """

    metadata: StrategyMetadata

    def scan(self, ctx: StrategyContext) -> StrategyScanResult: ...


__all__ = [
    "ALLOWED_CONFIDENCES",
    "ALLOWED_DIRECTIONS",
    "ALLOWED_HORIZONS",
    "ALLOWED_SCAN_STATUSES",
    "ALLOWED_STRATEGY_STATUSES",
    "Strategy",
    "StrategyContext",
    "StrategyMetadata",
    "StrategyScanResult",
    "StrategySignal",
]
