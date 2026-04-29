"""Tests for Finnhub news scoring, dedupe, and blackout overlays."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import pytz

from backend.notifications.finnhub_feed import FinnhubFeed

NY = pytz.timezone("America/New_York")


@pytest.fixture()
def fh_config(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_finnhub")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("IBKR_ACCOUNT", "DU000001")
    from backend.config import load_config

    p = Path(__file__).resolve().parents[1] / "config.example.yaml"
    return load_config(p)


@pytest.mark.asyncio
async def test_economic_calendar_graceful_without_client(fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    econ, err = await fh.economic_events_today()
    assert econ == []
    assert err is not None


@pytest.mark.asyncio
async def test_placeholder_api_key_feed_does_not_crash(monkeypatch) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("FINNHUB_API_KEY", "your_placeholder")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("IBKR_ACCOUNT", "DU000001")
    from backend.config import load_config

    p = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = load_config(p)

    fh = FinnhubFeed(cfg)

    async def run() -> None:
        await fh.start([])

    await run()
    assert fh._client is None
    assert fh.is_finnhub_live() is False


def test_score_meets_gate_watch_keyword_rth(fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    wl = {"SPY"}
    pos: set[str] = set()
    now_et = NY.localize(datetime(2026, 4, 29, 10, 0, 0))
    item = {
        "headline": "Fed guidance cut after CPI surprise",
        "summary": "",
    }
    sc, rs = fh.score_news_item(
        "SPY",
        item,
        now_et=now_et,
        open_position_symbols=pos,
        watch_symbols=wl,
    )
    assert sc >= 60
    assert len(rs) >= 2


def test_below_gate_no_boosters(fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    now_et = NY.localize(datetime(2026, 4, 29, 3, 0, 0))
    item = {"headline": "Quiet markets overnight", "summary": ""}
    sc, _rs = fh.score_news_item(
        "ZZZ",
        item,
        now_et=now_et,
        open_position_symbols=set(),
        watch_symbols=set(),
    )
    assert sc < 60


def test_headline_duplicate_fp_tracked(fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    h = "Breaking: merger chatter"
    fp = fh._headline_fp("X", h)
    assert not fh._already_emitted(fp)
    fh._register_emitted_fp(fp)
    assert fh._already_emitted(fp)


@pytest.mark.asyncio
async def test_refresh_high_score_notify_once_per_headline(
    monkeypatch, fh_config
) -> None:
    fh = FinnhubFeed(fh_config)
    fh.watchlist = ["AAA"]

    async def fake_to_thread(fn, *a, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "calendar_economic":
            return {}
        if name == "earnings_calendar":
            return {"earningsCalendar": []}
        if name == "company_news":
            dup = {
                "headline": "AAA merger talks escalate",
                "summary": "",
                "source": "testsrc",
                "url": "",
            }
            return [dup, dup]

        raise AssertionError(f"unexpected callable {fn!r}")

    monkeypatch.setattr(
        "backend.notifications.finnhub_feed.asyncio.to_thread",
        fake_to_thread,
    )

    class _StubCli:
        def calendar_economic(self, **_kw: object) -> dict[str, object]:
            return {}

        def earnings_calendar(self, **_kw: object) -> dict[str, object]:
            return {"earningsCalendar": []}

        def company_news(self, _sym: str, **_kw: object) -> list[dict[str, object]]:
            dup = {
                "headline": "AAA merger talks escalate",
                "summary": "",
                "source": "testsrc",
                "url": "",
            }
            return [dup, dup]

    fh._client = _StubCli()
    captured: list[list] = []

    def cap(ps):
        captured.append(list(ps))

    fh.set_high_impact_handler(cap)
    await fh.refresh(position_symbols=[])
    assert len(captured) == 1
    assert len(captured[0]) == 1


@pytest.mark.asyncio
async def test_earnings_full_day_blackout(monkeypatch, fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    fh.watchlist = ["SPY"]

    async def fake_to_thread(fn, *a, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "calendar_economic":
            return {}
        if name == "earnings_calendar":
            return {
                "earningsCalendar": [
                    {"symbol": "SPY", "epsEstimate": 1},
                ]
            }
        if name == "company_news":
            return []
        raise AssertionError(fn)

    monkeypatch.setattr(
        "backend.notifications.finnhub_feed.asyncio.to_thread",
        fake_to_thread,
    )

    class _StubCli:
        def calendar_economic(self, **_kw: object) -> dict[str, object]:
            return {}

        def earnings_calendar(self, **_kw: object) -> dict[str, object]:
            return {
                "earningsCalendar": [
                    {"symbol": "SPY", "epsEstimate": 1},
                ]
            }

        def company_news(self, _sym: str, **_kw: object) -> list[dict[str, object]]:
            return []

    fh._client = _StubCli()
    fh.set_high_impact_handler(None)
    await fh.refresh(position_symbols=[])

    m = fh.blackout_map()
    assert m.get("SPY") is True


@pytest.mark.asyncio
async def test_keyword_blackout_recorded(monkeypatch, fh_config) -> None:
    fh = FinnhubFeed(fh_config)
    fh.watchlist = ["XYZ"]

    async def fake_to_thread(fn, *a, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "calendar_economic":
            return {}
        if name == "earnings_calendar":
            return {"earningsCalendar": []}
        if name == "company_news":
            return [{"headline": "XYZ Fed rate shock", "summary": "", "source": "t"}]
        raise AssertionError(fn)

    monkeypatch.setattr(
        "backend.notifications.finnhub_feed.asyncio.to_thread",
        fake_to_thread,
    )

    class _StubCli:
        def calendar_economic(self, **_kw: object) -> dict[str, object]:
            return {}

        def earnings_calendar(self, **_kw: object) -> dict[str, object]:
            return {"earningsCalendar": []}

        def company_news(self, _sym: str, **_kw: object) -> list[dict[str, object]]:
            return [{"headline": "XYZ Fed rate shock", "summary": "", "source": "t"}]

    fh._client = _StubCli()
    await fh.refresh(position_symbols=[])
    kw = [b for b in fh.active_blackouts() if "keyword" in b["reason"]]
    assert len(kw) == 1
