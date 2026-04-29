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


def estimate_notional_usd_approx(
    spec: FxPairSpec,
    *,
    units: float,
    mid_price: float,
    usd_per_jpy: float | None = None,
) -> float:
    """Rough USD notion for daily / per-trade caps (spot FX heuristic)."""

    qj = float(usd_per_jpy or (1.0 / 151.0))
    u = abs(float(units))
    mid = abs(float(mid_price))
    if spec.base == "USD":
        return float(u)
    if spec.quote == "USD":
        return float(u * mid)
    if spec.quote == "JPY":
        jpy_nominal = float(u * mid)
        return float(jpy_nominal * qj)
    return float(u * mid)


def shrink_units_for_notional_caps(
    spec: FxPairSpec,
    *,
    units_in: float,
    mid_price: float,
    max_trade_usd: float,
    pair_remaining_usd: float,
    daily_remaining_usd: float,
    usd_per_jpy: float | None,
) -> tuple[float, str]:
    """Reduce units until notional meets min(per-trade, pair-rem, daily-rem)."""

    lim = float(
        max(0.0, min(max_trade_usd, pair_remaining_usd, daily_remaining_usd))
    )
    if lim <= 0:
        return 0.0, "no_remaining_notional"

    u_hi = abs(float(units_in))
    if u_hi <= 0:
        return 0.0, "units_zero"

    if estimate_notional_usd_approx(
        spec, units=u_hi, mid_price=mid_price, usd_per_jpy=usd_per_jpy
    ) <= lim:
        return float(u_hi), "ok"

    lo, hi = 0.0, u_hi
    best = 0.0
    for _ in range(52):
        mid_u = (lo + hi) / 2.0
        n_u = estimate_notional_usd_approx(
            spec, units=mid_u, mid_price=mid_price, usd_per_jpy=usd_per_jpy
        )
        if n_u <= lim:
            best = mid_u
            lo = mid_u
        else:
            hi = mid_u
        if hi - lo < min(250.0, u_hi / 9000):
            break

    best = round(max(0.0, best))
    snap = round(best / 25000) * 25000 if best >= 12500 else best
    if snap > 0 and estimate_notional_usd_approx(
        spec, units=snap, mid_price=mid_price, usd_per_jpy=usd_per_jpy
    ) <= lim:
        return float(snap), "ok"

    last = estimate_notional_usd_approx(
        spec, units=best, mid_price=mid_price, usd_per_jpy=usd_per_jpy
    )
    if last <= lim:
        return float(best), "ok"
    return 0.0, "cannot_fit_under_notional_caps"


__all__ = [
    "estimate_units_for_risk",
    "FxSizeResult",
    "estimate_notional_usd_approx",
    "shrink_units_for_notional_caps",
]
