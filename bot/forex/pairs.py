"""Parse FX pairs and map to IBKR CASH IDEALPRO contracts (base + quote)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PAIR_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class FxPairSpec:
    display: str
    base: str
    quote: str

    @property
    def slug(self) -> str:
        """No slash — used for filenames (AUDUSD)."""
        return f"{self.base}{self.quote}"


def parse_pair(pair: str) -> FxPairSpec:
    s = (pair or "").strip().upper().replace(" ", "")
    if "/" not in s:
        raise ValueError(f"pair must look like AUD/USD, got {pair!r}")
    a, b = s.split("/", 1)
    if not (_PAIR_RE.match(a) and _PAIR_RE.match(b)):
        raise ValueError(f"invalid fx pair {pair!r}")
    return FxPairSpec(display=f"{a}/{b}", base=a, quote=b)


def ibkr_contract_args(spec: FxPairSpec) -> dict[str, str]:
    """IBKR spot FX: secType=CASH, symbol=base, currency=quote, exchange=IDEALPRO."""
    return {
        "symbol": spec.base,
        "sec_type": "CASH",
        "currency": spec.quote,
        "exchange": "IDEALPRO",
    }


def pip_size_for_pair(spec: FxPairSpec, *, mt: float | None = None) -> float:
    """Default pip unless minTick known (JPY quote = 0.01 else 0.0001)."""

    if spec.quote == "JPY":
        base = 0.01
    else:
        base = 0.0001
    if mt and mt > 0:
        return float(mt)
    return base


__all__ = ["FxPairSpec", "parse_pair", "ibkr_contract_args", "pip_size_for_pair"]
