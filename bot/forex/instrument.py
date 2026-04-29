"""Forex instrument metadata (IBKR CASH / IDEALPRO).

Separate from equity :class:`~bot.stock` paths — Strategy Lab ICT FX test only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .pairs import ibkr_contract_args, parse_pair, pip_size_for_pair

AssetClass = Literal["equity", "forex"]


@dataclass(frozen=True)
class Instrument:
    asset_class: AssetClass
    display_symbol: str
    ib_symbol: str
    ib_currency: str
    sec_type: str
    exchange: str
    min_tick: float | None
    pip_size: float
    pip_value_mode: str
    base_currency: str
    quote_currency: str

    @classmethod
    def from_pair_display(cls, pair: str, *, min_tick: float | None = None) -> "Instrument":
        spec = parse_pair(pair)
        mt = min_tick if min_tick is not None else pip_size_for_pair(spec)
        return cls(
            asset_class="forex",
            display_symbol=spec.display,
            ib_symbol=spec.base,
            ib_currency=spec.quote,
            sec_type=str(ibkr_contract_args(spec)["sec_type"]),
            exchange=str(ibkr_contract_args(spec)["exchange"]),
            min_tick=min_tick,
            pip_size=float(mt),
            pip_value_mode="quote_ccy_approx",
            base_currency=spec.base,
            quote_currency=spec.quote,
        )


__all__ = ["Instrument", "AssetClass"]
