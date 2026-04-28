"""Persisted dynamic-watchlist JSON shape (research-only builder)."""

from __future__ import annotations

import json

from bot.watchlist_builder import DynamicWatchlist, WatchlistCandidate


def test_dynamic_watchlist_to_dict_required_keys() -> None:
    wl = DynamicWatchlist(
        date="2026-04-01",
        source="static",
        symbols=[
            WatchlistCandidate(symbol="NVDA", reason=["static_core"]),
        ],
        missing_data=["relative_volume"],
    )
    d = wl.to_dict()
    assert d["date"] == "2026-04-01"
    assert d["source"] == "static"
    assert d["research_only"] is True
    assert d["execution_allowed"] is False
    assert "missing_data" in d
    assert "symbols" in d and isinstance(d["symbols"], list)
    sym0 = d["symbols"][0]
    assert sym0["symbol"] == "NVDA"
    assert "latest_price" in sym0
    assert "relative_volume" in sym0
    assert "reason" in sym0


def test_serialised_json_has_top_level_source_and_symbols() -> None:
    wl = DynamicWatchlist(
        date="2026-04-02",
        source="ibkr",
        symbols=[
            WatchlistCandidate(
                symbol="AAPL",
                reason=["static_core"],
                latest_price=180.5,
                relative_volume=1.25,
            )
        ],
    )
    raw = json.dumps(wl.to_dict(), indent=2)
    assert '"source": "ibkr"' in raw
    assert '"latest_price": 180.5' in raw
