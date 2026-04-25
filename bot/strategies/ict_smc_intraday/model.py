"""Dataclasses + classification constants for ICT/SMC Intraday V1.

Pure data layer — no broker / IBKR / network imports, no decision logic.
Intentionally loose typing on payload dicts so the JSON written to
``data/intraday_smc/`` is forward-compatible with future fields.

Signal categories (Prompt 13D):

* ``DAY_TRADE_READY_STRICT``        — 5m sweep+reclaim + 1m sweep + 1m
                                      MSS/ChoCH + 1m FVG (or strong
                                      displacement) + valid stop/target +
                                      R/R >= ``min_rr_strict``.
* ``DAY_TRADE_READY_AGGRESSIVE``    — 5m sweep+reclaim + 1m sweep + 1m
                                      MSS/ChoCH + 1m OB / breaker /
                                      reclaim retest (no FVG required) +
                                      valid stop/target + R/R >=
                                      ``min_rr_aggressive``.
* ``WATCH_ONLY``                    — 5m setup exists but the 1m trigger
                                      is missing OR the trade plan is
                                      incomplete (still waiting).
* ``INVALID_RISK``                  — full setup but stop/target invalid,
                                      stop too wide, R/R too low, or price
                                      too extended from entry.
* ``BLOCKED``                       — hard data gap (e.g. no 1m bars) or
                                      mandatory hard-gate failed.
* ``NO_SETUP``                      — no 5m sweep / no usable structure.
* ``ERROR``                         — adapter-level exception path.

The strategy NEVER places orders; ``execution_allowed`` is hard-coded
to ``False`` on every payload that leaves this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------
SIGNAL_DAY_TRADE_READY_STRICT = "DAY_TRADE_READY_STRICT"
SIGNAL_DAY_TRADE_READY_AGGRESSIVE = "DAY_TRADE_READY_AGGRESSIVE"
SIGNAL_WATCH_ONLY = "WATCH_ONLY"
SIGNAL_INVALID_RISK = "INVALID_RISK"
SIGNAL_BLOCKED = "BLOCKED"
SIGNAL_NO_SETUP = "NO_SETUP"
SIGNAL_ERROR = "ERROR"

ALLOWED_SIGNAL_CATEGORIES: frozenset[str] = frozenset(
    {
        SIGNAL_DAY_TRADE_READY_STRICT,
        SIGNAL_DAY_TRADE_READY_AGGRESSIVE,
        SIGNAL_WATCH_ONLY,
        SIGNAL_INVALID_RISK,
        SIGNAL_BLOCKED,
        SIGNAL_NO_SETUP,
        SIGNAL_ERROR,
    }
)

DIRECTION_LONG = "long"
DIRECTION_SHORT = "short"
DIRECTION_FLAT = "flat"

ALLOWED_DIRECTIONS: frozenset[str] = frozenset(
    {DIRECTION_LONG, DIRECTION_SHORT, DIRECTION_FLAT}
)

# Entry source priority (highest first).
ENTRY_SOURCE_FVG = "fvg"
ENTRY_SOURCE_OB = "order_block"
ENTRY_SOURCE_BREAKER = "breaker_or_reclaim"
ENTRY_SOURCE_NONE = "none"

ALLOWED_ENTRY_SOURCES: frozenset[str] = frozenset(
    {ENTRY_SOURCE_FVG, ENTRY_SOURCE_OB, ENTRY_SOURCE_BREAKER, ENTRY_SOURCE_NONE}
)


# ---------------------------------------------------------------------------
# Strategy ID + display name (kept here for any module that wants them
# without importing the adapter).
# ---------------------------------------------------------------------------
STRATEGY_KEY = "ict_smc_intraday_v1"
STRATEGY_NAME = "ICT/SMC Intraday Liquidity Reversal V1"
STRATEGY_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Risk defaults (Prompt 13D defaults; CLI / yaml may override).
# ---------------------------------------------------------------------------
DEFAULT_MIN_RR_STRICT = 1.5
DEFAULT_MIN_RR_AGGRESSIVE = 1.2
DEFAULT_MAX_STOP_DISTANCE_PCT = 1.2
DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT = 1.0
DEFAULT_STOP_BUFFER_PCT = 0.05  # added to swept low/high to give the stop room


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LiquidityLevel:
    """A liquidity pool we care about (swing high / low above or below price)."""

    side: str  # "buy_side" (above) or "sell_side" (below)
    price: float
    timestamp: str = ""
    timeframe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntradayContext:
    """Higher-timeframe context for an intraday setup.

    All fields are *soft* by default — the strategy does not hard-block
    on 4H/30m disagreement; it just downgrades confidence.
    """

    symbol: str
    bias_4h: str = "unknown"  # "up" / "down" / "neutral" / "unknown"
    bias_30m: str = "unknown"
    bias_5m: str = "unknown"
    premium_discount_30m: str = "unknown"  # "premium" / "discount" / "unknown"
    liquidity_levels: list[LiquidityLevel] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    bars_4h_count: int = 0
    bars_30m_count: int = 0
    bars_5m_count: int = 0
    bars_1m_count: int = 0
    data_source: str = "unknown"  # "ibkr" / "fixture" / "missing"
    missing_data: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["liquidity_levels"] = [l.to_dict() for l in self.liquidity_levels]
        return d


@dataclass
class FiveMinuteSetup:
    """The 5m setup zone produced by sweep + reclaim (and optional MSS/FVG/OB)."""

    found: bool = False
    direction: str = DIRECTION_FLAT
    sweep_index: int | None = None
    sweep_timestamp: str = ""
    swept_level_price: float | None = None
    reclaim_close: float | None = None
    mss_found: bool = False
    mss_pivot_price: float | None = None
    has_fvg: bool = False
    fvg_low: float | None = None
    fvg_high: float | None = None
    has_order_block: bool = False
    order_block_low: float | None = None
    order_block_high: float | None = None
    setup_zone_low: float | None = None
    setup_zone_high: float | None = None
    setup_kind: str = ENTRY_SOURCE_NONE  # FVG > OB > breaker_or_reclaim > none
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OneMinuteTrigger:
    """The 1m entry trigger after price returns to the 5m setup zone."""

    found: bool = False
    direction: str = DIRECTION_FLAT
    sweep_index: int | None = None
    sweep_timestamp: str = ""
    swept_level_price: float | None = None
    mss_found: bool = False
    mss_pivot_price: float | None = None
    entry_source: str = ENTRY_SOURCE_NONE
    fvg_low: float | None = None
    fvg_high: float | None = None
    ob_low: float | None = None
    ob_high: float | None = None
    has_displacement: bool = False  # large-bodied 1m close in trigger direction
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntradayTradePlan:
    """The proposed entry / stop / target derived from the 1m trigger."""

    valid: bool = False
    direction: str = DIRECTION_FLAT
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    risk_per_share: float | None = None
    reward_per_share: float | None = None
    risk_reward: float | None = None
    stop_distance_pct: float | None = None
    extension_from_entry_pct: float | None = None
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntradayEvaluation:
    """Top-level output of one symbol scan."""

    symbol: str
    strategy_id: str = STRATEGY_KEY
    paper_only: bool = True
    execution_allowed: bool = False  # hard invariant
    date: str = ""
    direction: str = DIRECTION_FLAT
    signal_category: str = SIGNAL_NO_SETUP
    score: float | None = None
    context: IntradayContext | None = None
    five_min_setup: FiveMinuteSetup | None = None
    one_min_trigger: OneMinuteTrigger | None = None
    trade_plan: IntradayTradePlan | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    next_condition_to_watch: str = ""
    explanation_zh: str = ""
    chart_paths: list[str] = field(default_factory=list)
    chart_error: str | None = None
    research_flags: list[str] = field(default_factory=list)
    data_source: str = "unknown"
    data_quality: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Hard invariants: execution NEVER allowed at this stage; paper-only.
        if self.execution_allowed:
            raise ValueError(
                "IntradayEvaluation.execution_allowed must be False (paper-only invariant)."
            )
        if not self.paper_only:
            raise ValueError(
                "IntradayEvaluation.paper_only must be True (paper-only invariant)."
            )
        if self.signal_category not in ALLOWED_SIGNAL_CATEGORIES:
            raise ValueError(
                f"signal_category={self.signal_category!r} not in {sorted(ALLOWED_SIGNAL_CATEGORIES)}"
            )
        if self.direction not in ALLOWED_DIRECTIONS:
            raise ValueError(
                f"direction={self.direction!r} not in {sorted(ALLOWED_DIRECTIONS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
            "date": self.date,
            "direction": self.direction,
            "signal_category": self.signal_category,
            "score": self.score,
            "context": self.context.to_dict() if self.context else None,
            "five_min_setup": (
                self.five_min_setup.to_dict() if self.five_min_setup else None
            ),
            "one_min_trigger": (
                self.one_min_trigger.to_dict() if self.one_min_trigger else None
            ),
            "trade_plan": self.trade_plan.to_dict() if self.trade_plan else None,
            "rejection_reasons": list(self.rejection_reasons),
            "next_condition_to_watch": self.next_condition_to_watch,
            "explanation_zh": self.explanation_zh,
            "chart_paths": list(self.chart_paths),
            "chart_error": self.chart_error,
            "research_flags": list(self.research_flags),
            "data_source": self.data_source,
            "data_quality": dict(self.data_quality),
        }


# ---------------------------------------------------------------------------
# Risk config helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntradayRiskConfig:
    """Resolved risk thresholds for the classifier.

    Built from ``StrategyContext.extras`` / ``config/strategies.yaml``
    plus the safe defaults above.
    """

    min_rr_strict: float = DEFAULT_MIN_RR_STRICT
    min_rr_aggressive: float = DEFAULT_MIN_RR_AGGRESSIVE
    max_stop_distance_pct: float = DEFAULT_MAX_STOP_DISTANCE_PCT
    max_extension_from_entry_pct: float = DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT
    stop_buffer_pct: float = DEFAULT_STOP_BUFFER_PCT

    @classmethod
    def from_extras(cls, extras: Mapping[str, Any] | None) -> "IntradayRiskConfig":
        e = dict(extras or {})
        risk = e.get("risk") if isinstance(e.get("risk"), dict) else {}
        return cls(
            min_rr_strict=_safe_float(
                risk.get("min_rr_strict"), DEFAULT_MIN_RR_STRICT
            ),
            min_rr_aggressive=_safe_float(
                risk.get("min_rr_aggressive"), DEFAULT_MIN_RR_AGGRESSIVE
            ),
            max_stop_distance_pct=_safe_float(
                risk.get("max_stop_distance_pct"), DEFAULT_MAX_STOP_DISTANCE_PCT
            ),
            max_extension_from_entry_pct=_safe_float(
                risk.get("max_extension_from_entry_pct"),
                DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT,
            ),
            stop_buffer_pct=_safe_float(
                risk.get("stop_buffer_pct"), DEFAULT_STOP_BUFFER_PCT
            ),
        )


def _safe_float(v: Any, default: float) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


__all__ = [
    "ALLOWED_DIRECTIONS",
    "ALLOWED_ENTRY_SOURCES",
    "ALLOWED_SIGNAL_CATEGORIES",
    "DEFAULT_MAX_EXTENSION_FROM_ENTRY_PCT",
    "DEFAULT_MAX_STOP_DISTANCE_PCT",
    "DEFAULT_MIN_RR_AGGRESSIVE",
    "DEFAULT_MIN_RR_STRICT",
    "DEFAULT_STOP_BUFFER_PCT",
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
]
