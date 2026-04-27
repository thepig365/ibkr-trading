"""Optional REST news providers. Env var names only — keys never logged."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

from .base import NewsHeadline, ProviderCallResult

_TIMEOUT = 12


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "StrategyLab/1"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ssl.create_default_context()) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


class FinnhubProvider:
    name = "finnhub"

    def fetch_market_news(self) -> ProviderCallResult:
        k = (os.environ.get("FINNHUB_API_KEY") or "").strip()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "FINNHUB_API_KEY", [])
        try:
            q = urllib.parse.urlencode({"category": "general", "token": k})
            data = _get_json(f"https://finnhub.io/api/v1/news?{q}")
            if not isinstance(data, list):
                return ProviderCallResult(self.name, "failed", "unexpected JSON", [])
            out: list[NewsHeadline] = []
            for row in data[:30]:
                if not isinstance(row, dict):
                    continue
                t = str(row.get("headline") or row.get("title") or "").strip()
                if not t:
                    continue
                out.append(
                    NewsHeadline(
                        title=t,
                        url=str(row.get("url") or ""),
                        source="finnhub",
                        published_utc=str(row.get("datetime") or "")[:19],
                        tags=["market_moving"] if "fed" in t.lower() or "cpi" in t.lower() else ["other"],
                    )
                )
            return ProviderCallResult(self.name, "ok", f"{len(out)} items", out)
        except (OSError, json.JSONDecodeError, urllib.error.URLError) as e:
            return ProviderCallResult(self.name, "failed", str(e)[:200], [])

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult:
        k = (os.environ.get("FINNHUB_API_KEY") or "").strip()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "FINNHUB_API_KEY", [])
        out: list[NewsHeadline] = []
        today = date.today()
        d_from = (today - timedelta(days=2)).isoformat()
        d_to = today.isoformat()
        for sym in symbols[:25]:
            sym = sym.strip().upper()
            if not sym:
                continue
            try:
                q = urllib.parse.urlencode(
                    {"symbol": sym, "from": d_from, "to": d_to, "token": k}
                )
                data = _get_json(f"https://finnhub.io/api/v1/company-news?{q}")
                if not isinstance(data, list):
                    continue
                for row in data[:5]:
                    if not isinstance(row, dict):
                        continue
                    t = str(row.get("headline") or "").strip()
                    if not t:
                        continue
                    out.append(
                        NewsHeadline(
                            title=t,
                            url=str(row.get("url") or ""),
                            symbol=sym,
                            source="finnhub",
                            tags=["watchlist"],
                        )
                    )
            except (OSError, json.JSONDecodeError, urllib.error.URLError):
                continue
        return ProviderCallResult(self.name, "ok" if out else "ok", f"{len(out)} sym items", out)

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult(self.name, "skipped_missing_credentials", "use FMP for earnings in v1", [])

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult(self.name, "skipped_missing_credentials", "use manual macro calendar", [])


class FmpProvider:
    name = "fmp"

    def _key(self) -> str:
        return (os.environ.get("FMP_API_KEY") or "").strip()

    def fetch_market_news(self) -> ProviderCallResult:
        return ProviderCallResult(self.name, "skipped_missing_credentials", "FMP: use symbol calls", [])

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult:
        k = self._key()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "FMP_API_KEY", [])
        out: list[NewsHeadline] = []
        for sym in symbols[:15]:
            sym = sym.strip().upper()
            if not sym:
                continue
            try:
                q = urllib.parse.urlencode(
                    {
                        "tickers": sym,
                        "apikey": k,
                    }
                )
                data = _get_json(
                    f"https://financialmodelingprep.com/api/v3/stock_news?{q}"
                )
                if not isinstance(data, list):
                    continue
                for row in data[:3]:
                    if not isinstance(row, dict):
                        continue
                    t = str(row.get("title") or "").strip()
                    if not t:
                        continue
                    out.append(
                        NewsHeadline(
                            title=t,
                            url=str(row.get("url") or ""),
                            symbol=sym,
                            source="fmp",
                            tags=["watchlist"],
                        )
                    )
            except (OSError, json.JSONDecodeError, urllib.error.URLError):
                continue
        st = "ok" if out else "ok"
        return ProviderCallResult(self.name, st, f"{len(out)}", out)

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        k = self._key()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "FMP_API_KEY", [])
        # v1: keep stub — full earnings calendar can be added with the bulk FMP endpoint
        return ProviderCallResult(self.name, "ok", "earnings not queried in v1", [])

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult:  # noqa: ARG002
        return ProviderCallResult(self.name, "skipped_missing_credentials", "n/a", [])


class BenzingaProvider:
    name = "benzinga"

    def fetch_market_news(self) -> ProviderCallResult:
        k = (os.environ.get("BENZINGA_API_KEY") or "").strip()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "BENZINGA_API_KEY", [])
        return ProviderCallResult(self.name, "failed", "not implemented in v1", [])

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()


class PolygonProvider:
    name = "polygon"

    def fetch_market_news(self) -> ProviderCallResult:
        k = (os.environ.get("POLYGON_API_KEY") or "").strip()
        if not k:
            return ProviderCallResult(self.name, "skipped_missing_credentials", "POLYGON_API_KEY", [])
        return ProviderCallResult(self.name, "failed", "not implemented in v1", [])

    def fetch_symbol_news(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()

    def fetch_earnings_calendar(self, symbols: list[str]) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()

    def fetch_macro_calendar(self, *, day: date) -> ProviderCallResult:  # noqa: ARG002
        return self.fetch_market_news()
