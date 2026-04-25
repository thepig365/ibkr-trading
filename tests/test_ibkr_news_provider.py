"""Tests for the IBKR news provider (Prompt 13B PART B)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import load_config
from bot.research_intelligence import NewsCatalyst
from bot.research_providers.ibkr_news_provider import (
    IBKRNewsProviderStatus,
    fetch_ibkr_news,
    get_provider_status,
    read_latest_news_cache,
    write_news_cache,
)


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------
def test_module_does_not_import_broker_or_place_order() -> None:
    src = Path(
        __import__("bot.research_providers.ibkr_news_provider", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    assert "place_order" not in src
    assert "from ..broker" not in src


def test_status_without_client_does_not_connect() -> None:
    """get_provider_status with client=None must NOT open any socket."""

    # Synthesise a minimal AppConfig-like object without touching IBKR.
    cfg = SimpleNamespace()
    status = get_provider_status(cfg, client=None)
    assert status.ibkr_news_available is False
    assert status.providers_detected == []
    assert any("not connected" in n.lower() for n in status.notes)


# ---------------------------------------------------------------------------
# Behaviour with stub IBKRClient
# ---------------------------------------------------------------------------
class _StubClientNoEntitlement:
    """Mimics ib_async behaviour when there is no news subscription."""

    def get_news_providers(self) -> list[str]:
        return []  # IBKR returns no providers

    def get_historical_news(self, symbol, provider_codes=None, max_results=5):  # noqa: ARG002
        raise AssertionError(
            "fetch_ibkr_news must not call get_historical_news when no entitlement"
        )


class _StubClientWithProviders:
    def __init__(self, headlines: list) -> None:
        self._headlines = headlines

    def get_news_providers(self) -> list[str]:
        return ["BRFG", "DJ-N"]

    def get_historical_news(self, symbol, provider_codes=None, max_results=5):  # noqa: ARG002
        return [h for h in self._headlines if getattr(h, "symbol", None) == symbol][:max_results]


def test_missing_entitlement_does_not_crash(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    catalysts, status = fetch_ibkr_news(
        cfg,
        symbols=["AAPL", "TSLA"],
        client=_StubClientNoEntitlement(),
        limit_per_symbol=10,
    )
    assert catalysts == []
    assert status.ibkr_news_available is False
    assert isinstance(status.missing_entitlements, list)
    # We expect informative notes when no provider is available.
    assert status.notes


def test_provider_status_included_in_payload(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    headlines = [
        SimpleNamespace(
            symbol="NVDA",
            provider_code="BRFG",
            article_id="a1",
            headline="NVDA AI infrastructure deal expands",
            time_utc="2026-04-25T14:30:00Z",
        ),
    ]
    client = _StubClientWithProviders(headlines)
    catalysts, status = fetch_ibkr_news(
        cfg, symbols=["NVDA"], client=client, limit_per_symbol=5
    )
    assert status.ibkr_news_available is True
    assert "BRFG" in status.providers_detected
    assert len(catalysts) == 1
    nc = catalysts[0]
    assert isinstance(nc, NewsCatalyst)
    assert nc.symbol == "NVDA"
    assert nc.provider == "BRFG"


def test_cache_writes_under_data_research_only(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    status = IBKRNewsProviderStatus(
        ibkr_news_available=True,
        providers_detected=["BRFG"],
        missing_entitlements=[],
        notes=[],
        checked_at_utc="2026-04-25T00:00:00Z",
    )
    catalysts = [
        NewsCatalyst(
            timestamp="2026-04-25T14:30:00Z",
            provider="BRFG",
            article_id="a1",
            symbol="NVDA",
            headline="NVDA earnings beat",
        )
    ]
    out_path = write_news_cache(cfg, catalysts=catalysts, status=status)
    assert out_path.exists()
    rel = out_path.relative_to(tmp_project)
    parts = rel.parts
    assert parts[0] == "data"
    assert parts[1] == "research"
    assert parts[2] == "cache"
    assert parts[3] == "ibkr_news"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provider_status"]["ibkr_news_available"] is True
    assert payload["catalysts"][0]["symbol"] == "NVDA"


def test_read_latest_news_cache_round_trip(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    status = IBKRNewsProviderStatus(
        ibkr_news_available=False,
        providers_detected=[],
        missing_entitlements=[],
        notes=["nope"],
        checked_at_utc="2026-04-25T00:00:00Z",
    )
    write_news_cache(cfg, catalysts=[], status=status)
    cached = read_latest_news_cache(cfg)
    assert cached is not None
    assert cached["provider_status"]["ibkr_news_available"] is False


def test_read_latest_news_cache_missing_returns_none(tmp_project: Path) -> None:
    cfg = load_config(project_root=tmp_project)
    assert read_latest_news_cache(cfg) is None
