from __future__ import annotations

from datetime import date

from .base import ProviderCallResult


class NullNewsProvider:
    name = "null"

    def fetch_market_news(self) -> ProviderCallResult:
        return ProviderCallResult("null", "skipped_missing_credentials", "not configured", [])

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult("null", "skipped_missing_credentials", "not configured", [])

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult("null", "skipped_missing_credentials", "not configured", [])

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult("null", "skipped_missing_credentials", "not configured", [])
