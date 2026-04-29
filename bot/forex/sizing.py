"""Forex sizing: units based on approximate risk USD (not equity share count)."""

from __future__ import annotations

from dataclasses import dataclass

from .pairs import FxPairSpec


@dataclass
class FxSizeResult:
    units: float
    pip_distance: float
    risk_quote_ccy_approx: float
    sizing_available: bool
    reason_if_unavailable: str


def estimate_units_for_risk(
    spec: FxPairSpec,
    *,
    equity_usd: float,
    risk_pct: float,
    entry: float,
    stop: float,
    max_units: float,
    pip_size: float,
) -> FxSizeResult:
    risk_budget = abs(float(equity_usd)) * abs(float(risk_pct)) / 100.0
    pip_distance = abs(float(entry) - float(stop)) / float(pip_size)
    if pip_distance <= 0:
        return FxSizeResult(0.0, 0.0, 0.0, False, "pip_distance_zero")

    if spec.quote == "JPY":
        pip_val_per_unit = 0.01
        units_raw = risk_budget / max(pip_distance * pip_val_per_unit, 1e-12)
    else:
        pip_val_per_unit = float(pip_size)
        units_raw = risk_budget / max(pip_distance * pip_val_per_unit, 1e-12)

    u = min(max(float(units_raw), 25000), float(max_units))
    u = round(u / 25000) * 25000 if u >= 25000 else round(u)

    ok = True
    reason = ""
    if units_raw <= 0:
        ok = False
        reason = "units_non_positive"
    return FxSizeResult(
        units=float(u),
        pip_distance=float(pip_distance),
        risk_quote_ccy_approx=risk_budget,
        sizing_available=ok,
        reason_if_unavailable=reason,
    )


__all__ = ["estimate_units_for_risk", "FxSizeResult"]
