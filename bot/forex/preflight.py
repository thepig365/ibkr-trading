"""Forex LMT bracket preflight: Decimal tick rounding + geometry (execution compliance)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ticks import MinTickResolution, decimal_price, round_bracket_prices_decimal


@dataclass
class FxPreflight:
    ok: bool
    reasons: list[str]


@dataclass
class FxBracketPreflight:
    """Rounded-bracket audit consumed by paper_submit (submit uses rounded fields only)."""

    ok: bool
    reasons: list[str]
    original_entry: str
    original_stop: str
    original_target: str
    rounded_entry: str
    rounded_stop: str
    rounded_target: str
    min_tick: str
    min_tick_source: str
    rounding_mode: str
    geometry_ok: bool
    price_rounding_audit: dict[str, Any] = field(default_factory=dict)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "original_entry": self.original_entry,
            "original_stop": self.original_stop,
            "original_target": self.original_target,
            "rounded_entry": self.rounded_entry,
            "rounded_stop": self.rounded_stop,
            "rounded_target": self.rounded_target,
            "entry": self.rounded_entry,
            "stop": self.rounded_stop,
            "target": self.rounded_target,
            "min_tick": self.min_tick,
            "min_tick_source": self.min_tick_source,
            "rounding_mode": self.rounding_mode,
            "geometry_ok": self.geometry_ok,
            "price_rounding_audit": self.price_rounding_audit,
        }


def preflight_rounded_forex_bracket(
    *,
    direction: str,
    original_entry: float | str | Any,
    original_stop: float | str | Any,
    original_target: float | str | Any,
    tick: MinTickResolution,
    order_type: str,
    entry_rounding_mode: str = "nearest",
) -> FxBracketPreflight:
    """Round entry/stop/target to ``min_tick``, then validate long/short bracket geometry."""

    reasons: list[str] = []
    ot = (order_type or "").upper()
    if ot != "LMT":
        reasons.append("order_type_must_be_LMT")

    oe = decimal_price(original_entry)
    os_ = decimal_price(original_stop)
    otg = decimal_price(original_target)

    audit: dict[str, Any] = {
        "entry_mode": entry_rounding_mode,
        "stop_rule": "floor_below_entry_long_or_ceil_above_short",
        "target_rule": "ceil_above_long_or_floor_below_short",
    }

    try:
        re, rs, rt = round_bracket_prices_decimal(
            direction=direction,
            entry=oe,
            stop=os_,
            target=otg,
            min_tick=tick.min_tick,
            entry_mode=entry_rounding_mode,
        )
    except (ArithmeticError, ValueError) as exc:
        reasons.append(f"rounding_error:{exc}")
        return FxBracketPreflight(
            ok=False,
            reasons=reasons,
            original_entry=str(oe),
            original_stop=str(os_),
            original_target=str(otg),
            rounded_entry=str(oe),
            rounded_stop=str(os_),
            rounded_target=str(otg),
            min_tick=str(tick.min_tick),
            min_tick_source=tick.source,
            rounding_mode=entry_rounding_mode,
            geometry_ok=False,
            price_rounding_audit={**audit, "error": str(exc)},
        )

    d = (direction or "").lower()
    geometry_ok = False
    if d == "long":
        geometry_ok = rs < re < rt
        if not geometry_ok:
            reasons.append("forex_invalid_rounded_bracket_geometry")
    elif d == "short":
        geometry_ok = rt < re < rs
        if not geometry_ok:
            reasons.append("forex_invalid_rounded_bracket_geometry")
    else:
        reasons.append("direction_invalid")

    ok_bool = ot == "LMT" and geometry_ok and ("direction_invalid" not in reasons)

    audit_out = {
        **audit,
        "rounded_entry_dec": str(re),
        "rounded_stop_dec": str(rs),
        "rounded_target_dec": str(rt),
    }

    return FxBracketPreflight(
        ok=ok_bool,
        reasons=reasons,
        original_entry=str(oe),
        original_stop=str(os_),
        original_target=str(otg),
        rounded_entry=str(re),
        rounded_stop=str(rs),
        rounded_target=str(rt),
        min_tick=str(tick.min_tick),
        min_tick_source=tick.source,
        rounding_mode=entry_rounding_mode,
        geometry_ok=geometry_ok,
        price_rounding_audit=audit_out,
    )


def validate_bracket(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    min_tick: float,
    order_type: str,
) -> FxPreflight:
    """Legacy shim: bracket rules + tick grid (float API used by older tests)."""

    mt_use = float(min_tick) if min_tick and float(min_tick) > 0 else 0.00005
    res = MinTickResolution(decimal_price(str(mt_use)), "fallback")
    p = preflight_rounded_forex_bracket(
        direction=direction,
        original_entry=entry,
        original_stop=stop,
        original_target=target,
        tick=res,
        order_type=order_type,
        entry_rounding_mode="nearest",
    )
    return FxPreflight(ok=p.ok, reasons=p.reasons)


__all__ = [
    "FxPreflight",
    "FxBracketPreflight",
    "preflight_rounded_forex_bracket",
    "validate_bracket",
]
