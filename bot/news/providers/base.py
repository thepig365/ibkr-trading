"""Optional news / calendar providers (REST, env keys only; no trading side effects)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass
class NewsHeadline:
    title: str
    url: str = ""
    symbol: str = ""
    source: str = ""
    published_utc: str = ""
    tags: list[str] = field(default_factory=list)
    body_hint: str = ""


@dataclass
class ProviderCallResult:
    name: str
    status: str  # ok | skipped_missing_credentials | failed
    detail: str = ""
    items: list[NewsHeadline] = field(default_factory=list)


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    def fetch_market_news(self) -> ProviderCallResult: ...

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult: ...

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult: ...

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult: ...
