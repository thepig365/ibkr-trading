"""Optional news providers: missing API keys skip safely."""

from __future__ import annotations

import os
from unittest.mock import patch

from bot.news.providers.stub_providers import BenzingaProvider, FinnhubProvider, FmpProvider


def test_finnhub_skips_without_key() -> None:
    with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}, clear=False):
        p = FinnhubProvider()
        r = p.fetch_market_news()
    assert r.status == "skipped_missing_credentials"


def test_fmp_skips_without_key() -> None:
    with patch.dict(os.environ, {"FMP_API_KEY": ""}, clear=False):
        p = FmpProvider()
        r = p.fetch_symbol_news(["AAPL"])
    assert r.status == "skipped_missing_credentials"


def test_benzinga_skips_without_key() -> None:
    with patch.dict(os.environ, {"BENZINGA_API_KEY": ""}, clear=False):
        p = BenzingaProvider()
        r = p.fetch_market_news()
    assert r.status == "skipped_missing_credentials"
