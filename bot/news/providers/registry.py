from __future__ import annotations

import os

from .base import NewsHeadline, ProviderCallResult
from .stub_providers import (
    BenzingaProvider,
    FinnhubProvider,
    FmpProvider,
    PolygonProvider,
)


def all_providers() -> list:
    """All optional REST providers (no secrets in source)."""
    return [
        FinnhubProvider(),
        FmpProvider(),
        BenzingaProvider(),
        PolygonProvider(),
    ]


def dedupe_headlines(items: list[NewsHeadline]) -> list[NewsHeadline]:
    seen: set[tuple[str, str, str]] = set()
    out: list[NewsHeadline] = []
    for h in items:
        key = (h.title.strip().lower(), (h.url or "").strip(), (h.symbol or "").upper())
        if key in seen or not h.title.strip():
            continue
        seen.add(key)
        out.append(h)
    return out


def aggregate_provider_status(results: list[ProviderCallResult]) -> dict[str, str]:
    return {r.name: r.status for r in results}
