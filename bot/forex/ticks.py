"""Forex IDEALPRO minimum tick resolution + Decimal rounding (IBKR compliance)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from .pairs import FxPairSpec, ibkr_contract_args, parse_pair

if TYPE_CHECKING:
    from bot.config import AppConfig

__all__ = [
    "MinTickResolution",
    "decimal_price",
    "round_price_to_tick",
    "round_bracket_prices_decimal",
    "get_forex_min_tick",
    "resolve_forex_min_tick",
    "fetch_ibkr_contract_min_tick",
]


def decimal_price(v: Decimal | str | float | int) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@dataclass(frozen=True)
class MinTickResolution:
    min_tick: Decimal
    source: str  # ibkr | config | fallback


def _fallback_min_tick_decimal(spec: FxPairSpec, fb: dict[str, Any] | None) -> tuple[Decimal, str]:
    """YAML ``execution.forex_tick_fallback`` or pair-class defaults."""

    cfg = fb if isinstance(fb, dict) else {}
    cfg_block = cfg.get("forex_tick_fallback")
    if isinstance(cfg_block, dict):
        if spec.quote == "JPY":
            raw = cfg_block.get("jpy_quote")
        else:
            raw = cfg_block.get("non_jpy_quote")
        if raw is not None:
            return decimal_price(raw), "config"
    # IDEALPRO: half-pip common on majors; JPY crosses often 0.01
    if spec.quote == "JPY":
        return Decimal("0.01"), "fallback"
    return Decimal("0.00005"), "fallback"


def get_forex_min_tick(
    pair: str,
    *,
    contract_details: Any | None = None,
    fallback_config: dict[str, Any] | None = None,
) -> Decimal:
    """Return minimum tick — prefer IBKR-qualified contract ``minTick``."""

    src, _lbl = resolve_forex_min_tick(
        pair, contract_details=contract_details, fallback_config=fallback_config
    )
    return src.min_tick


def resolve_forex_min_tick(
    pair: str,
    *,
    contract_details: Any | None = None,
    fallback_config: dict[str, Any] | None = None,
) -> MinTickResolution:
    spec = parse_pair(pair)

    mt_attr = getattr(contract_details, "minTick", None) if contract_details is not None else None
    if mt_attr is not None:
        try:
            d = decimal_price(mt_attr)
            if d > 0:
                return MinTickResolution(d, "ibkr")
        except (ArithmeticError, ValueError, TypeError):
            pass

    d_fb, lbl = _fallback_min_tick_decimal(spec, fallback_config)
    return MinTickResolution(d_fb, lbl)


def fetch_ibkr_contract_min_tick(cfg: "AppConfig", spec: FxPairSpec, *, timeout: float = 12.0):
    """Connect (read/write session), qualify CASH IDEALPRO, return (qualified_contract | None, err)."""

    from bot.ibkr_client import IBKRClient
    from bot.ibkr_client_ids import FOREX_FETCH
    from bot.ibkr_connection import ibkr_client_collision_message, with_ibkr_client_id

    args = ibkr_contract_args(spec)
    last_exc: BaseException | None = None
    client: IBKRClient | None = None
    for step in range(3):
        cid = FOREX_FETCH + step
        sub = with_ibkr_client_id(cfg, cid)
        cl = IBKRClient(sub)
        try:
            cl.connect(readonly=False, timeout=timeout)
            client = cl
            break
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if ibkr_client_collision_message(exc) and step < 2:
                continue
            raise
    if client is None:
        return None, repr(last_exc) if last_exc else "connect_failed"
    try:
        from ib_async import Contract  # type: ignore

        ib = client._ib
        if ib is None:
            return None, "ib_none"
        c_kw: dict[str, Any] = {
            "symbol": args["symbol"],
            "currency": args["currency"],
            "exchange": args["exchange"],
            "secType": args["sec_type"],
        }
        c = Contract(**c_kw)
        qualified = ib.qualifyContracts(c)
        if not qualified:
            return None, "qualify_failed"
        return qualified[0], None
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def round_price_to_tick(price: Decimal | str | float | int, min_tick: Decimal, mode: str) -> Decimal:
    """Snap price to nearest tick multiple using Decimal (no IEEE float drift)."""

    px = decimal_price(price)
    mt = decimal_price(min_tick)
    if mt <= 0:
        raise ValueError("min_tick must be positive")

    ratio = px / mt
    if mode == "nearest":
        n = ratio.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    elif mode == "floor":
        n = ratio.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    elif mode == "ceil":
        n = ratio.quantize(Decimal("1"), rounding=ROUND_CEILING)
    else:
        raise ValueError(f"unknown rounding mode {mode!r}")

    out = n * mt
    # Strip exponent noise while keeping tick precision
    try:
        q = Decimal("1e%s" % mt.as_tuple().exponent) if mt.as_tuple().exponent < 0 else Decimal("1")
        return out.quantize(q)
    except Exception:
        return out


def round_bracket_prices_decimal(
    *,
    direction: str,
    entry: Decimal | str | float,
    stop: Decimal | str | float,
    target: Decimal | str | float,
    min_tick: Decimal,
    entry_mode: str = "nearest",
) -> tuple[Decimal, Decimal, Decimal]:
    """Apply long/short bracket rounding rules; may still fail geometry (caller validates)."""

    d = (direction or "").lower()
    mt = decimal_price(min_tick)
    e0, s0, t0 = decimal_price(entry), decimal_price(stop), decimal_price(target)

    if d == "long":
        e = round_price_to_tick(e0, mt, entry_mode)
        s = round_price_to_tick(s0, mt, "floor")
        t = round_price_to_tick(t0, mt, "ceil")
        guard = 5000
        while s >= e and guard:
            s -= mt
            guard -= 1
        guard = 5000
        while t <= e and guard:
            t += mt
            guard -= 1
        return e, s, t

    if d == "short":
        e = round_price_to_tick(e0, mt, entry_mode)
        s = round_price_to_tick(s0, mt, "ceil")
        t = round_price_to_tick(t0, mt, "floor")
        guard = 5000
        while s <= e and guard:
            s += mt
            guard -= 1
        guard = 5000
        while t >= e and guard:
            t -= mt
            guard -= 1
        return e, s, t

    raise ValueError("direction must be long or short")
