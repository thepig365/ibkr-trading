"""SMC/ICT timeframe registry.

Prompt 10A — Add 30min SMC/ICT Test Mode Only.

This module defines the small read-only registry that maps a timeframe
label (``daily`` or ``30min``) to:

* the IBKR historical-data request parameters used to fetch candles
  (``duration``, ``bar_size``, ``useRTH``),
* the per-timeframe strategy thresholds applied on top of the base
  SMC strategy block (``max_allowed_stop_pct``, ``max_extension_pct``,
  ``min_risk_reward``, ``risk_per_trade_pct``, ``lookback_period_for_sweep``),
* optional session filters (``avoid_first_minutes_after_open`` /
  ``avoid_last_minutes_before_close`` / ``max_hold_bars``) that
  demote READY/NEAR_ENTRY review candidates during the dangerous
  first/last 15 minutes of the US RTH session.

Safety invariants
-----------------
* No code path here imports :mod:`bot.broker`.
* No order placement. ``execution_allowed`` stays ``False``.
* Adding a new timeframe is a *read-only* act — it only changes how
  candles are pulled from IBKR and how risk numbers get scaled, never
  whether the bot can trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("daily", "30min")


# ---------------------------------------------------------------------------
# IBKR candle-fetch presets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TimeframeSpec:
    """Fully resolved timeframe specification used by scanner + CLI."""

    name: str
    enabled: bool
    duration: str
    bar_size: str
    use_rth: bool
    min_bars: int
    max_bars: int
    what_to_show: str = "TRADES"

    @property
    def is_intraday(self) -> bool:
        return self.name != "daily"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "duration": self.duration,
            "bar_size": self.bar_size,
            "use_rth": self.use_rth,
            "min_bars": self.min_bars,
            "max_bars": self.max_bars,
            "what_to_show": self.what_to_show,
            "is_intraday": self.is_intraday,
        }


DEFAULT_TIMEFRAME_SPECS: dict[str, dict[str, Any]] = {
    "daily": {
        "enabled": True,
        "duration": "1 Y",
        "bar_size": "1 day",
        "use_rth": True,
        "min_bars": 150,
        "max_bars": 400,
        "what_to_show": "TRADES",
    },
    "30min": {
        "enabled": True,
        "duration": "20 D",
        "bar_size": "30 mins",
        "use_rth": True,
        "min_bars": 100,
        "max_bars": 300,
        "what_to_show": "TRADES",
    },
}


# ---------------------------------------------------------------------------
# Per-timeframe strategy threshold overrides
# ---------------------------------------------------------------------------
DEFAULT_TIMEFRAME_STRATEGY: dict[str, dict[str, Any]] = {
    "daily": {
        "lookback_period_for_sweep": 20,
        "max_allowed_stop_pct": 5.0,
        "max_extension_pct": 3.0,
        "min_risk_reward": 2.0,
        "risk_per_trade_pct": 1.0,
    },
    "30min": {
        "lookback_period_for_sweep": 20,
        "max_allowed_stop_pct": 2.0,
        "max_extension_pct": 1.0,
        "min_risk_reward": 1.8,
        "risk_per_trade_pct": 0.25,
        "avoid_first_minutes_after_open": 15,
        "avoid_last_minutes_before_close": 15,
        "max_hold_bars": 13,
    },
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def normalise_timeframe(name: str | None) -> str:
    """Return a supported timeframe label. Unknown → ``'daily'``.

    The CLI and tests both accept ``daily`` and ``30min`` only. Anything
    else is normalised to ``daily`` rather than raising, so legacy
    callers that pass ``'1d'`` / ``''`` / ``None`` still work.
    """
    if not name:
        return "daily"
    key = str(name).strip().lower()
    aliases = {
        "1d": "daily",
        "d": "daily",
        "daily": "daily",
        "30m": "30min",
        "30 min": "30min",
        "30mins": "30min",
        "30 mins": "30min",
        "30min": "30min",
    }
    return aliases.get(key, "daily")


def resolve_timeframe_spec(
    name: str | None,
    cfg: Any | None = None,
) -> TimeframeSpec:
    """Return the fully-resolved IBKR candle preset for ``name``.

    Reads ``settings.smc_timeframes`` from the AppConfig when provided;
    missing keys fall back to the defaults above. Unknown timeframes
    fall back to ``daily``.
    """
    tf = normalise_timeframe(name)
    base = dict(DEFAULT_TIMEFRAME_SPECS[tf])
    overrides = _user_timeframes(cfg).get(tf) or {}
    for k, v in overrides.items():
        if v is not None:
            base[k] = v
    return TimeframeSpec(
        name=tf,
        enabled=bool(base.get("enabled", True)),
        duration=str(base.get("duration", "1 Y")),
        bar_size=str(base.get("bar_size", "1 day")),
        use_rth=bool(base.get("use_rth", True)),
        min_bars=int(base.get("min_bars", 100)),
        max_bars=int(base.get("max_bars", 300)),
        what_to_show=str(base.get("what_to_show", "TRADES")),
    )


def resolve_strategy_thresholds(
    name: str | None,
    strategy_block: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the per-timeframe strategy thresholds for ``name``.

    ``strategy_block`` should be the resolved
    ``strategies.SMC_LIQUIDITY_REVERSAL_RESEARCH`` mapping.

    Resolution rules (kept deliberately conservative so legacy configs
    that do not know about ``timeframes`` keep working):

    * ``daily``: return ``{}`` unless the strategy block explicitly
      declares a ``timeframes.daily`` entry. This preserves the pre-
      Prompt-10A behaviour where the single base block drove
      everything.
    * ``30min``: always start from :data:`DEFAULT_TIMEFRAME_STRATEGY`
      and shallow-merge any declared overrides on top. 30min is a
      new research mode and *must* apply its stricter defaults.
    """
    tf = normalise_timeframe(name)
    tf_overrides_raw = (
        ((strategy_block or {}).get("timeframes") or {}).get(tf)
    )
    tf_overrides = {k: v for k, v in (tf_overrides_raw or {}).items()
                    if v is not None}
    if tf == "daily" and not tf_overrides:
        return {}
    base = dict(DEFAULT_TIMEFRAME_STRATEGY[tf])
    base.update(tf_overrides)
    return base


def apply_thresholds_to_block(
    strategy_block: dict[str, Any], thresholds: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a *new* strategy block with the per-timeframe overrides baked in.

    The caller passes the shallow-merged strategy block and the dict
    returned by :func:`resolve_strategy_thresholds`. We translate the
    flat threshold keys into the strategy block's nested schema so
    downstream evaluators do not need to know about the per-timeframe
    layer.
    """
    out: dict[str, Any] = {**strategy_block}
    if not thresholds:
        out["_timeframe_thresholds"] = {}
        return out
    for key, target_keys in _THRESHOLD_MAPPING.items():
        if key not in thresholds:
            continue
        value = thresholds[key]
        for path in target_keys:
            section, field_name = path
            current = dict(out.get(section) or {})
            current[field_name] = value
            out[section] = current
    # Preserve the original per-timeframe dict so downstream scorers can
    # look it up without re-running :func:`resolve_strategy_thresholds`.
    out["_timeframe_thresholds"] = dict(thresholds)
    return out


_THRESHOLD_MAPPING: dict[str, tuple[tuple[str, str], ...]] = {
    "lookback_period_for_sweep": (("sweep", "lookback_period"),),
    "max_allowed_stop_pct": (("stop", "max_allowed_stop_pct"),),
    "max_extension_pct": (
        ("entry", "reject_if_price_extended_from_entry_pct"),
    ),
    "min_risk_reward": (
        ("risk", "min_reward_to_risk"),
        ("target", "min_risk_reward"),
    ),
    "risk_per_trade_pct": (
        ("risk", "max_account_risk_per_trade_pct"),
    ),
}


# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------
@dataclass
class SessionGuard:
    """Decision + reason for whether a READY/NEAR_ENTRY can be emitted.

    ``allowed`` is ``True`` when the guard does not fire; ``False`` when
    the scan is inside the avoid window and the classifier must demote
    the row. ``reason`` is a short English explanation used in
    ``review_notes``.
    """

    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


def evaluate_session_guard(
    timeframe: str,
    *,
    now_et_hhmm: str | None,
    thresholds: Mapping[str, Any] | None = None,
) -> SessionGuard:
    """Return a :class:`SessionGuard` for ``timeframe`` at ``now_et_hhmm``.

    Parameters
    ----------
    timeframe:
        Normalised TF label. ``daily`` → always allowed.
    now_et_hhmm:
        Current US Eastern clock expressed as ``'HH:MM'``. When ``None``
        (no real clock is supplied), the guard defaults to *allowed*
        so backward-compatibility tests keep passing — the CLI layer
        is responsible for supplying the real clock when needed.
    thresholds:
        The per-timeframe thresholds dict. When omitted we fall back
        to the module defaults for ``30min``.
    """
    tf = normalise_timeframe(timeframe)
    if tf != "30min":
        return SessionGuard(allowed=True, reason="")
    if not now_et_hhmm:
        return SessionGuard(allowed=True, reason="")
    thr = dict(thresholds or DEFAULT_TIMEFRAME_STRATEGY["30min"])
    first_block = int(thr.get("avoid_first_minutes_after_open", 15) or 0)
    last_block = int(thr.get("avoid_last_minutes_before_close", 15) or 0)
    try:
        hh, mm = (int(x) for x in now_et_hhmm.split(":", 1))
    except Exception:  # noqa: BLE001 - treat malformed clock as "allowed"
        return SessionGuard(allowed=True, reason="")
    minutes = hh * 60 + mm
    rth_open = 9 * 60 + 30
    rth_close = 16 * 60
    if minutes < rth_open or minutes >= rth_close:
        # Outside RTH — 30min strategy requires RTH only; do not flip
        # READY there either.
        return SessionGuard(
            allowed=False,
            reason=(
                "30min session filter: outside US RTH "
                "(09:30–16:00 ET); READY/NEAR_ENTRY suppressed."
            ),
        )
    if minutes < rth_open + first_block:
        return SessionGuard(
            allowed=False,
            reason=(
                f"30min session filter: within first {first_block}m "
                "of RTH open; READY/NEAR_ENTRY suppressed."
            ),
        )
    if minutes >= rth_close - last_block:
        return SessionGuard(
            allowed=False,
            reason=(
                f"30min session filter: within last {last_block}m "
                "before RTH close; READY/NEAR_ENTRY suppressed."
            ),
        )
    return SessionGuard(allowed=True, reason="")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _user_timeframes(cfg: Any | None) -> dict[str, dict[str, Any]]:
    if cfg is None:
        return {}
    settings = getattr(cfg, "settings", None)
    if settings is None:
        return {}
    tf_cfg = getattr(settings, "smc_timeframes", None)
    if not tf_cfg:
        return {}
    # tf_cfg is a pydantic model — convert to dict.
    if hasattr(tf_cfg, "model_dump"):
        dumped = tf_cfg.model_dump()
    elif isinstance(tf_cfg, dict):
        dumped = tf_cfg
    else:
        return {}
    # Filter out ``None`` entries so defaults win.
    return {
        k: {kk: vv for kk, vv in (v or {}).items() if vv is not None}
        for k, v in (dumped or {}).items()
        if v
    }


__all__ = [
    "DEFAULT_TIMEFRAME_SPECS",
    "DEFAULT_TIMEFRAME_STRATEGY",
    "SUPPORTED_TIMEFRAMES",
    "SessionGuard",
    "TimeframeSpec",
    "apply_thresholds_to_block",
    "evaluate_session_guard",
    "normalise_timeframe",
    "resolve_strategy_thresholds",
    "resolve_timeframe_spec",
]
