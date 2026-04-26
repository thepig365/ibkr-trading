"""Sort ticker edge profiles for ranking display (Prompt 13L-alt)."""

from __future__ import annotations

from .ticker_edge import TickerEdgeProfile


def rank_profiles(
    profiles: list[TickerEdgeProfile], *, top_n: int | None = None
) -> list[TickerEdgeProfile]:
    """Higher ``edge_score`` first; tie-breaker symbol A-Z."""
    s = sorted(
        profiles,
        key=lambda p: (-p.edge_score, p.symbol),
    )
    if top_n is not None and top_n > 0:
        return s[: int(top_n)]
    return s
