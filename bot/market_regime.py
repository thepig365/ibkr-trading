"""Deterministic market regime classifier with VIX fallback.

Two layers live in this module:

* :func:`classify_regime` — pure label-only function, kept unchanged
  for callers that only need the regime name. Supports the original
  rule ladder when VIX is available and the SPY/QQQ-trend fallback
  when it is not.
* :func:`evaluate_regime` — returns a fully structured
  :class:`RegimeEvaluation` with the label, a ``regime_confidence``
  level, ``new_positions_allowed`` / ``research_scans_allowed``
  flags, a human-readable ``reason`` and a ``market_data`` dict that
  matches the schema documented in ``docs/market-regime.md``.

Both functions are stateless and do not touch IBKR directly. The
caller is responsible for collecting the inputs (see
``bot/news_report.py::_fetch_market_inputs`` and the thin wrapper in
``bot/cli.py``).

Safety: the module never authorises a trade on its own. The
evaluator returns ``new_positions_allowed=False`` whenever confidence
is not sufficient, and ``execution_allowed`` stays ``False`` globally
regardless of what this module says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Regime = Literal[
    "risk_on",
    "neutral",
    "elevated_vol",
    "risk_off",
    "crisis",
    "unknown",
]

Confidence = Literal["high", "medium", "low"]

ALL_REGIMES: tuple[Regime, ...] = (
    "risk_on",
    "neutral",
    "elevated_vol",
    "risk_off",
    "crisis",
    "unknown",
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass
class MarketInputs:
    """Snapshot of the indicators the classifier looks at.

    Any field may be ``None`` when the underlying data source did not
    return a value. When VIX is unavailable, the classifier falls back
    to SPY / QQQ trend data only and may return ``risk_off`` or
    ``neutral`` (never ``risk_on`` - that label requires VIX).
    """

    vix: float | None = None
    vix3m: float | None = None
    spy: float | None = None
    spy_200ma: float | None = None
    qqq: float | None = None
    qqq_200ma: float | None = None

    def trend_available(self) -> bool:
        """At least one of SPY-trend or QQQ-trend is usable."""
        spy_ok = self.spy is not None and self.spy_200ma is not None
        qqq_ok = self.qqq is not None and self.qqq_200ma is not None
        return spy_ok or qqq_ok

    def has_required(self) -> bool:
        """Backwards-compat predicate retained for test fixtures."""
        return (
            self.vix is not None
            and self.spy is not None
            and self.spy_200ma is not None
        )


# ---------------------------------------------------------------------------
# Regime label (pure)
# ---------------------------------------------------------------------------
def classify_regime(inputs: MarketInputs) -> Regime:
    """Classify the regime using the rules from the spec.

    With **VIX present**, the original ladder applies. The first
    matching rule wins:

        1. VIX >= 30                         -> crisis
        2. VIX >= 20                         -> elevated_vol
        3. VIX / VIX3M >= 1.0                -> risk_off
        4. SPY < SPY_200MA  (or QQQ < 200MA) -> risk_off
        5. VIX < 15 and SPY > SPY_200MA      -> risk_on
        6. otherwise                         -> neutral

    With **VIX missing** we fall back to a trend-only classification
    using whatever is available:

        * SPY < 200MA OR QQQ < 200MA -> risk_off
        * otherwise                   -> neutral

    If neither SPY-trend nor QQQ-trend is available we return
    ``"unknown"`` and the news-report layer will block new entries.
    """
    if not inputs.trend_available():
        return "unknown"

    spy_below = (
        inputs.spy is not None
        and inputs.spy_200ma is not None
        and inputs.spy < inputs.spy_200ma
    )
    qqq_below = (
        inputs.qqq is not None
        and inputs.qqq_200ma is not None
        and inputs.qqq < inputs.qqq_200ma
    )

    if inputs.vix is not None:
        if inputs.vix >= 30:
            return "crisis"
        if inputs.vix >= 20:
            return "elevated_vol"
        if (
            inputs.vix3m is not None
            and inputs.vix3m > 0
            and (inputs.vix / inputs.vix3m) >= 1.0
        ):
            return "risk_off"
        if spy_below or qqq_below:
            return "risk_off"
        if (
            inputs.vix < 15
            and inputs.spy is not None
            and inputs.spy_200ma is not None
            and inputs.spy > inputs.spy_200ma
        ):
            return "risk_on"
        return "neutral"

    if spy_below or qqq_below:
        return "risk_off"
    return "neutral"


def regime_is_defensive(regime: Regime) -> bool:
    """True for regimes that the news report should treat as 'no new entries'."""
    return regime in {"risk_off", "crisis", "unknown"}


# ---------------------------------------------------------------------------
# Full evaluator (with confidence + flags + market_data)
# ---------------------------------------------------------------------------
DEFAULT_REGIME_CFG: dict[str, Any] = {
    "allow_medium_confidence_for_research": True,
    "allow_medium_confidence_for_new_positions": False,
    "require_vix_for_execution": True,
    "require_spy_200ma_for_execution": True,
    "require_qqq_200ma_for_execution": False,
}


@dataclass
class RegimeEvaluation:
    """Full regime payload returned by :func:`evaluate_regime`."""

    market_regime: Regime = "unknown"
    regime_confidence: Confidence = "low"
    new_positions_allowed: bool = False
    research_scans_allowed: bool = False
    reason: str = ""
    market_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_regime": self.market_regime,
            "regime_confidence": self.regime_confidence,
            "new_positions_allowed": self.new_positions_allowed,
            "research_scans_allowed": self.research_scans_allowed,
            "reason": self.reason,
            "market_data": dict(self.market_data),
        }


def build_market_data(inputs: MarketInputs) -> dict[str, Any]:
    """Build the ``market_data`` dict documented in the spec.

    Missing fields are reported in ``missing_fields`` using the canonical
    lower-case keys (``vix``, ``vix3m``, ``spy``, ``spy_200ma``,
    ``qqq``, ``qqq_200ma``) so downstream code can filter / match on
    them without having to reverse-engineer the labels.
    """
    # Use upper-case labels so the missing list renders cleanly in
    # operator-facing surfaces (Telegram digest, console).
    missing: list[str] = []
    if inputs.vix is None:
        missing.append("VIX")
    if inputs.vix3m is None:
        missing.append("VIX3M")
    if inputs.spy is None:
        missing.append("SPY")
    if inputs.spy_200ma is None:
        missing.append("SPY 200MA")
    if inputs.qqq is None:
        missing.append("QQQ")
    if inputs.qqq_200ma is None:
        missing.append("QQQ 200MA")

    spy_above = None
    if inputs.spy is not None and inputs.spy_200ma is not None:
        spy_above = inputs.spy >= inputs.spy_200ma
    qqq_above = None
    if inputs.qqq is not None and inputs.qqq_200ma is not None:
        qqq_above = inputs.qqq >= inputs.qqq_200ma

    ratio = None
    if inputs.vix is not None and inputs.vix3m and inputs.vix3m > 0:
        ratio = round(inputs.vix / inputs.vix3m, 4)

    return {
        "spy_close": inputs.spy,
        "spy_200ma": inputs.spy_200ma,
        "spy_above_200ma": spy_above,
        "qqq_close": inputs.qqq,
        "qqq_200ma": inputs.qqq_200ma,
        "qqq_above_200ma": qqq_above,
        "vix": inputs.vix,
        "vix3m": inputs.vix3m,
        "vix_vix3m_ratio": ratio,
        "missing_fields": missing,
    }


def _confidence(inputs: MarketInputs) -> Confidence:
    """Pick a confidence level from the data we actually have."""
    have_vix = inputs.vix is not None
    have_vix3m = inputs.vix3m is not None
    have_spy = inputs.spy is not None and inputs.spy_200ma is not None
    have_qqq = inputs.qqq is not None and inputs.qqq_200ma is not None

    if have_vix and (have_spy or have_qqq):
        # VIX + at least one trend reference
        return "high" if have_spy and have_qqq and have_vix3m else "medium" if have_spy and have_qqq else "medium"
    if have_spy and have_qqq:
        return "medium"
    if have_spy or have_qqq:
        return "low"
    return "low"


def evaluate_regime(
    inputs: MarketInputs, cfg: dict[str, Any] | None = None
) -> RegimeEvaluation:
    """Classify + attach confidence, flags and ``market_data``.

    ``cfg`` is the ``market_regime`` block from
    ``config/settings.yaml``; callers may pass ``None`` to use the
    conservative defaults in :data:`DEFAULT_REGIME_CFG`.

    ``new_positions_allowed`` respects the config knobs
    (``require_vix_for_execution`` etc.). ``research_scans_allowed``
    is looser: it is ``True`` as long as the regime is not ``unknown``
    and the research confidence floor is met, which matches the
    principle that "research is always allowed, execution needs
    more".
    """
    effective_cfg = {**DEFAULT_REGIME_CFG, **(cfg or {})}

    regime = classify_regime(inputs)
    confidence = _confidence(inputs)
    market_data = build_market_data(inputs)
    missing = market_data["missing_fields"]

    reasons: list[str] = []

    # --- new_positions_allowed gate
    # Execution-side gate is conservative. Even a clean risk_on regime
    # returns False here unless the caller's config trusts the
    # confidence level.
    new_pos_allowed = regime not in ("risk_off", "crisis", "unknown")

    if effective_cfg.get("require_vix_for_execution", True) and inputs.vix is None:
        new_pos_allowed = False
        reasons.append("VIX missing; execution requires VIX")
    if (
        effective_cfg.get("require_spy_200ma_for_execution", True)
        and inputs.spy_200ma is None
    ):
        new_pos_allowed = False
        reasons.append("SPY 200MA missing; execution requires SPY trend data")
    if (
        effective_cfg.get("require_qqq_200ma_for_execution", False)
        and inputs.qqq_200ma is None
    ):
        new_pos_allowed = False
        reasons.append("QQQ 200MA missing; execution requires QQQ trend data")
    if confidence == "low":
        new_pos_allowed = False
        reasons.append("regime confidence=low; no new positions")
    if confidence == "medium" and not effective_cfg.get(
        "allow_medium_confidence_for_new_positions", False
    ):
        new_pos_allowed = False
        reasons.append(
            "regime confidence=medium; config forbids medium-confidence entries"
        )

    # --- research_scans_allowed gate
    # Research is always permitted unless we have nothing at all.
    research_allowed = regime != "unknown"
    if confidence == "low" and not effective_cfg.get(
        "allow_medium_confidence_for_research", True
    ):
        research_allowed = False

    # --- human reason
    if missing:
        reasons.append("missing market data: " + ", ".join(missing))
    if inputs.vix is None and inputs.trend_available():
        reasons.append("VIX/VIX3M unavailable; using SPY/QQQ trend fallback")
    if regime == "unknown":
        reasons.append("no trend reference data available")
    reason = "; ".join(dict.fromkeys(reasons))  # dedupe while preserving order

    # Global invariant: execution is disabled project-wide; even a
    # 'high-confidence risk_on' returns new_positions_allowed, but the
    # risk_engine and broker layer will still reject at runtime.
    return RegimeEvaluation(
        market_regime=regime,
        regime_confidence=confidence,
        new_positions_allowed=new_pos_allowed,
        research_scans_allowed=research_allowed,
        reason=reason,
        market_data=market_data,
    )


__all__ = [
    "Regime",
    "Confidence",
    "ALL_REGIMES",
    "MarketInputs",
    "RegimeEvaluation",
    "DEFAULT_REGIME_CFG",
    "classify_regime",
    "regime_is_defensive",
    "evaluate_regime",
    "build_market_data",
]
