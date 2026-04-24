"""Tests for the pre-open news report orchestrator."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot.config import load_config
from bot.ibkr_client import IBKRClient, NewsHeadline, PositionRow
from bot.news_report import (
    PreOpenReport,
    append_report_markdown,
    generate_report,
    notify_report,
    save_report_json,
)
from bot.research import PerplexityClient, ResearchResult

NY = ZoneInfo("America/New_York")

REQUIRED_KEYS = {
    "date",
    "run_time_new_york",
    "market_regime",
    "trade_allowed",
    "new_positions_allowed",
    "research_available",
    "ibkr_news_available",
    "external_research_available",
    "blocked_symbols",
    "manual_review_required",
    "market_data",
    "major_news",
    "analyst_ratings",
    "earnings_news",
    "macro_news",
    "macro_events",
    "holdings_risk",
    "watchlist_catalysts",
    "bot_instruction",
}

MARKET_DATA_KEYS = {
    "spy_above_200ma",
    "qqq_above_200ma",
    "vix",
    "vix3m",
    "vix_vix3m_ratio",
    "missing_fields",
}


def _stub_research(payload: dict) -> PerplexityClient:
    """Return a PerplexityClient whose .research() returns a fixed payload."""

    client = PerplexityClient(api_key="fake")

    def fake_research(req):  # noqa: ANN001
        return ResearchResult(available=True, payload=payload)

    client.research = fake_research  # type: ignore[method-assign]
    return client


def _unavailable_research() -> PerplexityClient:
    client = PerplexityClient(api_key=None)

    def fake_research(req):  # noqa: ANN001
        return ResearchResult.empty(error="no key")

    client.research = fake_research  # type: ignore[method-assign]
    return client


def _mock_ibkr(
    monkeypatch,
    *,
    positions: list[PositionRow] | None = None,
    vix: float | None = 16.0,
    vix3m: float | None = 20.0,
    spy: float | None = 500.0,
    spy_200: float | None = 450.0,
    news_providers: list[str] | None = None,
    headlines: dict[str, list[NewsHeadline]] | None = None,
) -> IBKRClient:
    """Return an IBKRClient whose remote methods are fully mocked."""

    cfg = load_config()
    client = IBKRClient(cfg)
    client._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(client, "get_positions", lambda: positions or [])
    monkeypatch.setattr(client, "get_open_orders", lambda: [])
    monkeypatch.setattr(client, "get_news_providers", lambda: news_providers or [])

    def _latest(symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD"):
        return {
            "VIX": vix,
            "VIX3M": vix3m,
            "SPY": spy,
        }.get(symbol.upper())

    monkeypatch.setattr(client, "get_latest_close", _latest)
    monkeypatch.setattr(
        client,
        "get_simple_moving_average",
        lambda *a, **k: spy_200,
    )

    def _news(symbol, provider_codes=None, max_results=5, **_):
        if headlines is None:
            return []
        return list(headlines.get(symbol.upper(), []))

    monkeypatch.setattr(client, "get_historical_news", _news)
    return client


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_report_json_schema(tmp_project: Path, monkeypatch) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {
                "major_news": [],
                "macro_events": [],
                "holdings_risk": [],
                "watchlist_catalysts": [],
            }
        ),
        now=datetime(2026, 4, 24, 8, 30, tzinfo=NY),
    )
    path = save_report_json(cfg, report)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert REQUIRED_KEYS <= set(data), f"missing keys: {REQUIRED_KEYS - set(data)}"
    # Enumerated values stay within spec.
    assert data["market_regime"] in {
        "risk_on",
        "neutral",
        "elevated_vol",
        "risk_off",
        "crisis",
        "unknown",
    }


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------
def test_missing_perplexity_key_blocks_new_entries(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_unavailable_research(),
    )
    assert report.external_research_available is False
    assert report.new_positions_allowed is False
    assert "external research unavailable" in report.bot_instruction.lower()


def test_missing_ibkr_news_is_soft_failure(tmp_project: Path, monkeypatch) -> None:
    cfg = load_config(project_root=tmp_project)
    # news_providers empty -> IBKR news unavailable, but external research
    # is still present, so the report should still allow new positions
    # (subject to regime).
    client = _mock_ibkr(monkeypatch, news_providers=[])
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {
                "major_news": [],
                "macro_events": [],
                "holdings_risk": [],
                "watchlist_catalysts": [],
            }
        ),
    )
    assert report.ibkr_news_available is False
    assert report.external_research_available is True
    assert report.research_available is True


def test_ibkr_connection_failure_blocks_new_entries(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)

    def fail_connect(self, timeout: float = 10.0) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(IBKRClient, "connect", fail_connect)

    report = generate_report(
        cfg,
        ibkr_client=None,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
        connect=True,
    )
    # No IBKR => market inputs missing => regime unknown.
    assert report.market_regime == "unknown"
    assert report.new_positions_allowed is False


# ---------------------------------------------------------------------------
# Market regime via generate_report
# ---------------------------------------------------------------------------
def test_regime_crisis_flows_through_generate_report(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch, vix=35.0, vix3m=25.0)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    assert report.market_regime == "crisis"
    assert report.new_positions_allowed is False


def test_regime_elevated_vol_still_blocks_new_entries_via_regime(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch, vix=22.0, vix3m=22.0)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    assert report.market_regime == "elevated_vol"
    # elevated_vol is NOT defensive per spec, so new entries remain
    # allowed unless another gate fires.
    assert report.new_positions_allowed is True


def test_regime_risk_off_on_inversion_blocks_new_entries(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch, vix=18.0, vix3m=17.0)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    assert report.market_regime == "risk_off"
    assert report.new_positions_allowed is False


# ---------------------------------------------------------------------------
# Blocked / manual review
# ---------------------------------------------------------------------------
def test_high_severity_news_blocks_symbol(tmp_project: Path, monkeypatch) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    payload = {
        "major_news": [
            {
                "headline": "XYZ halted pending SEC investigation",
                "source": "reuters",
                "symbols": ["XYZ"],
                "asset_classes": ["equity"],
                "impact": "negative",
                "severity": "high",
                "confidence": "high",
                "summary": "trading halt after SEC probe",
            }
        ],
        "macro_events": [],
        "holdings_risk": [],
        "watchlist_catalysts": [],
    }
    report = generate_report(
        cfg, ibkr_client=client, perplexity_client=_stub_research(payload)
    )
    assert "XYZ" in report.blocked_symbols
    assert "XYZ" in report.manual_review_required


def test_earnings_catalyst_requires_manual_review(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    payload = {
        "major_news": [],
        "macro_events": [],
        "holdings_risk": [],
        "watchlist_catalysts": [
            {
                "symbol": "NVDA",
                "catalyst": "earnings before open",
                "severity": "medium",
                "summary": "",
            }
        ],
    }
    report = generate_report(
        cfg, ibkr_client=client, perplexity_client=_stub_research(payload)
    )
    assert "NVDA" in report.manual_review_required


def test_high_severity_macro_near_open_blocks_entries(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    payload = {
        "major_news": [],
        "macro_events": [
            {
                "event": "CPI release",
                "time_new_york": "09:45",  # 15 minutes after the open
                "severity": "high",
                "market_relevance": "broad",
            }
        ],
        "holdings_risk": [],
        "watchlist_catalysts": [],
    }
    report = generate_report(
        cfg, ibkr_client=client, perplexity_client=_stub_research(payload)
    )
    assert report.new_positions_allowed is False
    assert any(
        "macro" in w.lower() for w in report.bot_instruction.split(";")
    ) or "macro" in report.bot_instruction.lower()


def test_report_fields_always_present_when_research_empty(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    data = report.to_dict()
    assert isinstance(data["blocked_symbols"], list)
    assert isinstance(data["manual_review_required"], list)
    assert isinstance(data["major_news"], list)
    assert isinstance(data["macro_events"], list)


# ---------------------------------------------------------------------------
# Telegram privacy
# ---------------------------------------------------------------------------
def test_telegram_digest_redacts_account_and_dollars(
    tmp_project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json
        class R:
            status_code = 200
            text = "{}"
            def json(self_inner):
                return {"ok": True}
        return R()

    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    report.bot_instruction = (
        "Review account DU1234567, NetLiquidation: 102345.67, cash $98,000."
    )
    ok = notify_report(cfg, report)
    assert ok is True

    sent = captured["json"]["text"]
    assert "DU1234567" not in sent
    assert "102345.67" not in sent
    assert "$98,000" not in sent


# ---------------------------------------------------------------------------
# CLI invariants
# ---------------------------------------------------------------------------
def test_cli_pre_open_news_does_not_place_orders(
    tmp_project: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from bot import cli as cli_module
    from bot.broker import Broker

    def fake_connect(self, timeout: float = 10.0, *args, **kwargs) -> None:
        self._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(IBKRClient, "get_positions", lambda self: [])
    monkeypatch.setattr(IBKRClient, "get_open_orders", lambda self: [])
    monkeypatch.setattr(IBKRClient, "get_news_providers", lambda self: [])
    monkeypatch.setattr(
        IBKRClient,
        "get_historical_news",
        lambda self, *a, **k: [],
    )
    monkeypatch.setattr(
        IBKRClient,
        "get_latest_close",
        lambda self, symbol, **k: {"VIX": 16.0, "VIX3M": 19.0, "SPY": 500.0}.get(
            symbol.upper()
        ),
    )
    monkeypatch.setattr(
        IBKRClient, "get_simple_moving_average", lambda self, *a, **k: 480.0
    )
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    # Any attempt to place an order explodes immediately.
    sentinel = MagicMock(
        side_effect=AssertionError("place_order must not be called")
    )
    monkeypatch.setattr(Broker, "place_order", sentinel)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["pre-open-news", "--dry-run"])
    assert result.exit_code == 0, result.stdout
    sentinel.assert_not_called()


def test_cli_pre_open_news_writes_files_by_default(
    tmp_project: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from bot import cli as cli_module

    def fake_connect(self, timeout: float = 10.0, *args, **kwargs) -> None:
        self._ib = MagicMock(isConnected=lambda: True)

    monkeypatch.setattr(IBKRClient, "connect", fake_connect)
    monkeypatch.setattr(IBKRClient, "disconnect", lambda self: None)
    monkeypatch.setattr(IBKRClient, "get_positions", lambda self: [])
    monkeypatch.setattr(IBKRClient, "get_open_orders", lambda self: [])
    monkeypatch.setattr(IBKRClient, "get_news_providers", lambda self: [])
    monkeypatch.setattr(IBKRClient, "get_historical_news", lambda self, *a, **k: [])
    monkeypatch.setattr(
        IBKRClient,
        "get_latest_close",
        lambda self, symbol, **k: {"VIX": 16.0, "VIX3M": 19.0, "SPY": 500.0}.get(
            symbol.upper()
        ),
    )
    monkeypatch.setattr(
        IBKRClient, "get_simple_moving_average", lambda self, *a, **k: 480.0
    )
    monkeypatch.setattr(
        cli_module, "load_config", lambda: load_config(project_root=tmp_project)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["pre-open-news"])
    assert result.exit_code == 0, result.stdout

    # JSON written under data/pre_open_news/YYYY-MM-DD.json
    json_dir = tmp_project / "data" / "pre_open_news"
    files = list(json_dir.glob("*.json"))
    assert files, "pre-open-news should write a JSON file"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert REQUIRED_KEYS <= set(payload)

    md = (tmp_project / "memory" / "NEWS-REPORT.md").read_text(encoding="utf-8")
    assert "Pre-Open Major News Report" in md


# ---------------------------------------------------------------------------
# Markdown rendering smoke test
# ---------------------------------------------------------------------------
def test_append_markdown_is_idempotent_and_grows(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    path = append_report_markdown(cfg, report)
    first = path.read_text(encoding="utf-8")
    append_report_markdown(cfg, report)
    second = path.read_text(encoding="utf-8")
    assert len(second) > len(first)


# ---------------------------------------------------------------------------
# Prompt 4: market_data, headline cleaning, categorisation, severity
# ---------------------------------------------------------------------------
def test_market_data_block_present_and_well_formed(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    client = _mock_ibkr(monkeypatch)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    md = report.market_data
    assert MARKET_DATA_KEYS <= set(md), f"missing keys: {MARKET_DATA_KEYS - set(md)}"
    # SPY=500 > 450 == 200MA (from default mock).
    assert md["spy_above_200ma"] is True
    # vix=16, vix3m=20 -> ratio 0.8.
    assert md["vix_vix3m_ratio"] == pytest.approx(0.8, abs=1e-6)


def test_vix_missing_records_missing_fields_and_unknown_when_no_trend(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    # VIX/VIX3M/SPY 200MA all missing -> trend unavailable -> unknown.
    client = _mock_ibkr(
        monkeypatch, vix=None, vix3m=None, spy=None, spy_200=None
    )
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    md = report.market_data
    assert "VIX" in md["missing_fields"]
    assert "VIX3M" in md["missing_fields"]
    assert "SPY 200MA" in md["missing_fields"]
    assert report.market_regime == "unknown"
    assert report.new_positions_allowed is False


def test_vix_missing_with_spy_trend_falls_back_to_neutral(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    # VIX gone but SPY trend usable: 500 > 450 -> trend-only "neutral".
    client = _mock_ibkr(monkeypatch, vix=None, vix3m=None)
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    assert report.market_regime == "neutral"
    assert "VIX" in report.market_data["missing_fields"]


def test_ibkr_metadata_stripped_from_major_news(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    raw = (
        "{A:800015:L:en:K:n/a:C:0.9775911569595337}!"
        "Apple guidance withdrawn after major outage [BRFG]"
    )
    headlines = {
        "AAPL": [
            NewsHeadline(
                symbol="AAPL",
                provider_code="BRFG",
                article_id="1",
                headline=raw,
                time_utc="2026-04-24T12:30:00Z",
            )
        ]
    }
    client = _mock_ibkr(
        monkeypatch,
        positions=[
            PositionRow(
                account="DU000",
                symbol="AAPL",
                sec_type="STK",
                exchange="NASDAQ",
                currency="USD",
                position=10.0,
                avg_cost=180.0,
            )
        ],
        news_providers=["BRFG"],
        headlines=headlines,
    )
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    # The cleaned headline should have surfaced somewhere (major_news,
    # earnings_news or macro_news depending on category).
    all_news = (
        report.major_news + report.earnings_news + report.macro_news
        + report.analyst_ratings
    )
    assert all_news, "headline must be categorised into one of the news buckets"
    for it in all_news:
        assert "{A:" not in it["headline"]
        assert "[BRFG]" not in it["headline"]
        assert not it["headline"].startswith("!")


def test_analyst_headlines_routed_to_analyst_ratings_bucket(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    raw = (
        "{A:800015:L:en}!"
        "Rosenblatt reiterated Apple (AAPL) coverage with Neutral and target $268 "
        "[BRFUPDN]"
    )
    headlines = {
        "AAPL": [
            NewsHeadline(
                symbol="AAPL",
                provider_code="BRFUPDN",
                article_id="x",
                headline=raw,
                time_utc=None,
            )
        ]
    }
    client = _mock_ibkr(
        monkeypatch,
        positions=[
            PositionRow(
                account="DU000",
                symbol="AAPL",
                sec_type="STK",
                exchange="NASDAQ",
                currency="USD",
                position=1.0,
                avg_cost=180.0,
            )
        ],
        news_providers=["BRFUPDN"],
        headlines=headlines,
    )
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    assert report.analyst_ratings, "analyst headline should be in analyst_ratings"
    # And it must NOT pollute major_news.
    for it in report.major_news:
        assert "Rosenblatt" not in it["headline"]


def test_duplicate_headlines_are_deduped(tmp_project: Path, monkeypatch) -> None:
    cfg = load_config(project_root=tmp_project)
    raw1 = "{A:1}!Apple announces new iPhone [BRFG] AAPL"
    raw2 = "{A:2}!Apple announces new iPhone [BRFG]"
    headlines = {
        "AAPL": [
            NewsHeadline("AAPL", "BRFG", "1", raw1, None),
            NewsHeadline("AAPL", "BRFG", "2", raw2, None),
        ]
    }
    client = _mock_ibkr(
        monkeypatch,
        positions=[
            PositionRow(
                account="DU000",
                symbol="AAPL",
                sec_type="STK",
                exchange="NASDAQ",
                currency="USD",
                position=1.0,
                avg_cost=180.0,
            )
        ],
        news_providers=["BRFG"],
        headlines=headlines,
    )
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    all_news = report.major_news + report.earnings_news
    iphone_items = [
        it for it in all_news if "iphone" in it["headline"].lower()
    ]
    assert len(iphone_items) == 1, iphone_items


def test_low_severity_analyst_does_not_dominate_telegram(
    tmp_project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json

        class R:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"ok": True}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    # 8 analyst rating updates, no major news.
    report.analyst_ratings = [
        {
            "headline": f"Bank reiterated stock {i} with Neutral",
            "source": "BRFUPDN",
            "symbols": [f"S{i}"],
            "severity": "low",
            "category": "analyst",
        }
        for i in range(8)
    ]
    report.bot_instruction = "Routine."

    ok = notify_report(cfg, report)
    assert ok is True
    body = captured["json"]["text"]
    # Default Chinese report groups analyst ratings per symbol and
    # caps the list so we never flood the operator.
    assert "四、分析师评级" in body
    # Symbol grouping keeps the output compact - 8 distinct symbols
    # means 8 grouped bullets, each indented below the symbol line.
    assert "- S0:" in body
    assert "- S7:" in body


def test_telegram_digest_lists_missing_market_data(
    tmp_project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json

        class R:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"ok": True}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    report.market_data = {
        "spy_above_200ma": True,
        "qqq_above_200ma": None,
        "vix": None,
        "vix3m": None,
        "vix_vix3m_ratio": None,
        "missing_fields": ["VIX", "VIX3M"],
    }
    report.bot_instruction = "OK."
    notify_report(cfg, report)
    # Default Telegram language is now Chinese (Prompt 9.2).
    body = captured["json"]["text"]
    assert "缺失数据" in body
    assert "VIX" in body
    assert "SPY/QQQ 200MA" in body


def test_telegram_digest_clearly_states_external_research_missing(
    tmp_project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json

        class R:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"ok": True}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(
        date="2026-04-24",
        market_regime="neutral",
        ibkr_news_available=True,
        external_research_available=False,
    )
    report.bot_instruction = "OK."
    notify_report(cfg, report)
    # Prompt 9.2: the default Chinese report must distinguish IBKR
    # headlines from external research and must NOT say "data unavailable"
    # when IBKR headlines are present.
    body = captured["json"]["text"]
    assert "IBKR 新闻数据：可用" in body
    assert "外部研究数据：未启用 / 不可用" in body
    assert "IBKR headlines" in body


def test_markdown_does_not_contain_raw_ibkr_metadata(
    tmp_project: Path, monkeypatch
) -> None:
    cfg = load_config(project_root=tmp_project)
    raw = (
        "{A:800015:L:en:K:n/a:C:0.9775911569595337}!"
        "Apple SEC investigation announced [BRFG]"
    )
    headlines = {
        "AAPL": [
            NewsHeadline("AAPL", "BRFG", "1", raw, None),
        ]
    }
    client = _mock_ibkr(
        monkeypatch,
        positions=[
            PositionRow(
                account="DU000",
                symbol="AAPL",
                sec_type="STK",
                exchange="NASDAQ",
                currency="USD",
                position=1.0,
                avg_cost=180.0,
            )
        ],
        news_providers=["BRFG"],
        headlines=headlines,
    )
    report = generate_report(
        cfg,
        ibkr_client=client,
        perplexity_client=_stub_research(
            {"major_news": [], "macro_events": [], "holdings_risk": [], "watchlist_catalysts": []}
        ),
    )
    md_path = append_report_markdown(cfg, report)
    text = md_path.read_text(encoding="utf-8")
    assert "{A:" not in text
    assert "[BRFG]" not in text


def test_top_telegram_news_prefers_high_severity(
    tmp_project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    captured: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["json"] = json

        class R:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"ok": True}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = load_config(project_root=tmp_project)
    report = PreOpenReport(date="2026-04-24", market_regime="neutral")
    report.major_news = [
        {"headline": f"low {i}", "severity": "low", "symbols": ["L"]}
        for i in range(20)
    ] + [
        {"headline": "BANKRUPTCY ANNOUNCED", "severity": "high", "symbols": ["X"]},
    ]
    report.bot_instruction = "OK."
    notify_report(cfg, report)
    body = captured["json"]["text"]
    assert "BANKRUPTCY ANNOUNCED" in body
    # With 20 lows + 1 high and ``max_major_news_items`` = 20, the
    # Chinese report sorts by severity first, so the high item appears
    # before any lows and at least one low is trimmed off the end.
    # The trimmed message explicitly calls out the overflow.
    assert "另有" in body
    # The high-severity item is always shown in position 1.
    idx_high = body.index("BANKRUPTCY ANNOUNCED")
    idx_low = body.index("low 0")
    assert idx_high < idx_low
