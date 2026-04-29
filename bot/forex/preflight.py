"""Bracket geometry + rounding for Forex LMT brackets (structure only)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FxPreflight:
    ok: bool
    reasons: list[str]


def validate_bracket(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    min_tick: float,
    order_type: str,
) -> FxPreflight:
    reasons: list[str] = []
    d = (direction or "").lower()
    ot = (order_type or "").upper()

    if ot != "LMT":
        reasons.append("order_type_must_be_LMT")

    mt = float(min_tick) if min_tick and float(min_tick) > 0 else 0.00005

    def _r(x: float) -> float:
        steps = round(x / mt)
        return steps * mt

    e, s_, t = _r(entry), _r(stop), _r(target)

    if d == "long":
        if not (s_ < e < t):
            reasons.append("long_geometry_stop_entry_target")
    elif d == "short":
        if not (t < e < s_):
            reasons.append("short_geometry_target_entry_stop")
    else:
        reasons.append("direction_invalid")

    return FxPreflight(ok=len(reasons) == 0, reasons=reasons)


__all__ = ["validate_bracket", "FxPreflight"]
