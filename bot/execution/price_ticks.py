"""Min-tick price normalization for US-style equity brackets (Prompt 13J.1).

Uses :class:`decimal.Decimal` for stable rounding. Callers must use normalized
prices (never raw 4+ decimal unrounded US stock values) when ``minTick`` is
0.01 or similar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from ..strategies.ict_smc_intraday.model import DIRECTION_LONG, DIRECTION_SHORT

RoundingMode = Literal["nearest", "floor", "ceil"]

# IBKR / US common stock default when contract details are unavailable
MIN_TICK_US_STOCK_DEFAULT: Decimal = Decimal("0.01")


def _to_decimal(x: object) -> Decimal:
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(float(x)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TypeError(f"not numeric: {x!r}") from exc


def round_to_tick(price: object, min_tick: object, mode: RoundingMode) -> Decimal:
    """Round *price* to a multiple of *min_tick*.

    * ``nearest`` — half away from zero on the *tick step count*.
    * ``floor``  — lower multiple of *min_tick*.
    * ``ceil``   — higher multiple of *min_tick*.
    """
    p = _to_decimal(price)
    t = _to_decimal(min_tick)
    if t <= 0:
        raise ValueError("min_tick must be positive")
    n = p / t
    if mode == "nearest":
        r = n.to_integral_value(rounding=ROUND_HALF_UP)
    elif mode == "floor":
        r = n.to_integral_value(rounding=ROUND_FLOOR)
    elif mode == "ceil":
        r = n.to_integral_value(rounding=ROUND_CEILING)
    else:  # pragma: no cover
        raise ValueError(f"unknown mode {mode!r}")
    return (r * t).quantize(t)


@dataclass(frozen=True)
class BracketTickNormalization:
    original_entry: Decimal
    original_stop: Decimal
    original_target: Decimal
    entry: Decimal | None
    stop: Decimal | None
    target: Decimal | None
    min_tick: Decimal
    planned_rr_before: Decimal | None
    planned_rr_after: Decimal | None
    tick_rounding_applied: bool
    valid: bool
    rejection_reasons: list[str] = field(default_factory=list)
    # Original float inputs (for audit) — set by normalize_bracket_prices
    original_entry_f: float = 0.0
    original_stop_f: float = 0.0
    original_target_f: float = 0.0


def _rr_long(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    pr = entry - stop
    rw = target - entry
    if pr <= 0 or rw < 0:
        return Decimal("-1")
    return rw / pr


def _rr_short(entry: Decimal, stop: Decimal, target: Decimal) -> Decimal:
    pr = stop - entry
    rw = entry - target
    if pr <= 0 or rw < 0:
        return Decimal("-1")
    return rw / pr


def validate_normalized_bracket(
    direction: str,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    min_rr: Decimal,
) -> tuple[bool, list[str], Decimal | None]:
    """Return (ok, reasons, planned_rr) after all prices are on valid ticks."""
    reasons: list[str] = []
    d = (direction or "").strip().lower()
    rr: Decimal | None
    if d == DIRECTION_LONG:
        if not (stop < entry < target):
            reasons.append("invalid_long_geometry_after_normalization")
            return False, reasons, None
        rr = _rr_long(entry, stop, target)
    elif d == DIRECTION_SHORT:
        if not (target < entry < stop):
            reasons.append("invalid_short_geometry_after_normalization")
            return False, reasons, None
        rr = _rr_short(entry, stop, target)
    else:
        return False, [f"unknown direction {direction!r}"], None
    if rr is None or rr < 0:
        reasons.append("rr_below_min_after_rounding")
        return False, reasons, None
    if rr < min_rr:
        reasons.append("rr_below_min_after_rounding")
        return False, reasons, None
    return True, [], rr


def normalize_bracket_prices(
    direction: str,
    entry: object,
    stop: object,
    target: object,
    min_tick: object,
    min_rr: object,
) -> BracketTickNormalization:
    """Normalize entry/stop/target to *min_tick*; long/short per Prompt 13J.1.

    * LONG: entry nearest, stop floor (away from entry), target ceil.
    * SHORT: entry nearest, stop ceil, target floor.
    * Recomputes R/R after rounding; may reject with ``rr_below_min_after_rounding`` or
      ``invalid_after_tick_rounding``.
    """
    reasons: list[str] = []
    o_e = _to_decimal(entry)
    o_s = _to_decimal(stop)
    o_t = _to_decimal(target)
    t = _to_decimal(min_tick)
    mrr = _to_decimal(min_rr)
    o_ef = float(o_e)
    o_sf = float(o_s)
    o_tf = float(o_t)

    if t <= 0:
        t = MIN_TICK_US_STOCK_DEFAULT
        reasons.append("min_tick_invalid_used_default")

    d = (direction or "").strip().lower()
    rb: Decimal | None
    if d == DIRECTION_LONG:
        if not (o_s < o_e < o_t):
            return BracketTickNormalization(
                o_e,
                o_s,
                o_t,
                None,
                None,
                None,
                t,
                None,
                None,
                False,
                False,
                [f"invalid original geometry (long) stop={o_s} entry={o_e} target={o_t}"],
                o_ef,
                o_sf,
                o_tf,
            )
        rb = _rr_long(o_e, o_s, o_t)
    elif d == DIRECTION_SHORT:
        if not (o_t < o_e < o_s):
            return BracketTickNormalization(
                o_e,
                o_s,
                o_t,
                None,
                None,
                None,
                t,
                None,
                None,
                False,
                False,
                [f"invalid original geometry (short) target={o_t} entry={o_e} stop={o_s}"],
                o_ef,
                o_sf,
                o_tf,
            )
        rb = _rr_short(o_e, o_s, o_t)
    else:
        return BracketTickNormalization(
            o_e,
            o_s,
            o_t,
            None,
            None,
            None,
            t,
            None,
            None,
            False,
            False,
            [f"invalid direction {direction!r}"],
            o_ef,
            o_sf,
            o_tf,
        )

    e_adj = round_to_tick(o_e, t, "nearest")
    if d == DIRECTION_LONG:
        s_adj = round_to_tick(o_s, t, "floor")
        t_adj = round_to_tick(o_t, t, "ceil")
    else:
        s_adj = round_to_tick(o_s, t, "ceil")
        t_adj = round_to_tick(o_t, t, "floor")

    tick_rounding_applied = (e_adj != o_e) or (s_adj != o_s) or (t_adj != o_t)

    ok_geo, g_reasons, ra = validate_normalized_bracket(d, e_adj, s_adj, t_adj, mrr)
    if not ok_geo:
        reasons.extend(g_reasons)
        if not g_reasons:
            reasons.append("invalid_after_tick_rounding")
        return BracketTickNormalization(
            o_e,
            o_s,
            o_t,
            e_adj,
            s_adj,
            t_adj,
            t,
            rb,
            None,
            tick_rounding_applied,
            False,
            reasons
            or ["invalid_after_tick_rounding"],
            o_ef,
            o_sf,
            o_tf,
        )
    return BracketTickNormalization(
        o_e,
        o_s,
        o_t,
        e_adj,
        s_adj,
        t_adj,
        t,
        rb,
        ra,
        tick_rounding_applied,
        True,
        [],
        o_ef,
        o_sf,
        o_tf,
    )


__all__ = [
    "BracketTickNormalization",
    "MIN_TICK_US_STOCK_DEFAULT",
    "normalize_bracket_prices",
    "round_to_tick",
    "RoundingMode",
    "validate_normalized_bracket",
]
