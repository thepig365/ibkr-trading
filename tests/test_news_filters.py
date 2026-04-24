"""Unit tests for ``bot.news_filters``.

These cover the deterministic helpers that drive the pre-open news
report: headline cleaning, deduplication, categorisation and the
severity classifier.
"""

from __future__ import annotations

import pytest

from bot.news_filters import (
    categorize_headline,
    classify_severity,
    clean_ibkr_headline,
    dedupe_headlines,
)


# ---------------------------------------------------------------------------
# clean_ibkr_headline
# ---------------------------------------------------------------------------
class TestCleanIBKRHeadline:
    def test_strips_leading_metadata_brace(self) -> None:
        raw = (
            "{A:800015:L:en:K:n/a:C:0.9775911569595337}!"
            "Rosenblatt reiterated Apple (AAPL) coverage with Neutral and target $268"
        )
        cleaned = clean_ibkr_headline(raw)
        assert cleaned.startswith("Rosenblatt reiterated Apple")
        assert "{A:" not in cleaned
        assert not cleaned.startswith("!")

    def test_strips_provider_tags(self) -> None:
        raw = "Apple announces new iPhone [BRFG] [BRFUPDN]"
        assert clean_ibkr_headline(raw) == "Apple announces new iPhone"

    def test_strips_trailing_symbol_run(self) -> None:
        raw = "Apple announces new iPhone AAPL"
        assert "AAPL" not in clean_ibkr_headline(raw)

    def test_collapses_whitespace(self) -> None:
        raw = "Apple   announces    new   iPhone"
        assert clean_ibkr_headline(raw) == "Apple announces new iPhone"

    def test_empty_input(self) -> None:
        assert clean_ibkr_headline("") == ""
        assert clean_ibkr_headline("{A:1}") == ""

    def test_leaves_human_text_untouched(self) -> None:
        raw = "Fed signals patience on rate cuts"
        assert clean_ibkr_headline(raw) == raw


# ---------------------------------------------------------------------------
# dedupe_headlines
# ---------------------------------------------------------------------------
class TestDedupeHeadlines:
    def test_drops_exact_duplicates(self) -> None:
        items = [
            {"headline": "Apple announces new iPhone", "symbols": ["AAPL"]},
            {"headline": "Apple announces new iPhone", "symbols": ["AAPL"]},
        ]
        out = dedupe_headlines(items)
        assert len(out) == 1

    def test_merges_symbol_lists(self) -> None:
        items = [
            {"headline": "Apple announces new iPhone", "symbols": ["AAPL"]},
            {"headline": "Apple announces new iPhone!!", "symbols": ["MSFT"]},
        ]
        out = dedupe_headlines(items)
        assert len(out) == 1
        assert set(out[0]["symbols"]) == {"AAPL", "MSFT"}

    def test_higher_severity_wins(self) -> None:
        items = [
            {"headline": "Same news", "symbols": [], "severity": "low"},
            {"headline": "same news", "symbols": [], "severity": "high"},
        ]
        out = dedupe_headlines(items)
        assert out[0]["severity"] == "high"

    def test_drops_blank_headlines(self) -> None:
        items = [{"headline": "", "symbols": ["AAPL"]}]
        assert dedupe_headlines(items) == []


# ---------------------------------------------------------------------------
# categorize_headline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "headline,expected",
    [
        ("Rosenblatt reiterated AAPL with Neutral and target $268", "analyst"),
        ("Goldman Sachs upgrades MSFT to Buy", "analyst"),
        ("Wedbush raises price target on TSLA", "analyst"),
        ("NVDA beats consensus on earnings", "earnings"),
        ("AAPL Q3 results above guidance", "earnings"),
        ("Fed signals patience on rate cuts", "macro"),
        ("CPI prints hotter than expected", "macro"),
        ("OPEC raises production targets", "macro"),
        ("XYZ halted pending SEC investigation", "major"),
        ("Boeing announces new partnership", "major"),
    ],
)
def test_categorize_headline(headline: str, expected: str) -> None:
    assert categorize_headline(headline) == expected


# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "headline",
    [
        "XYZ halted pending SEC investigation",
        "Acme files for Chapter 11 bankruptcy",
        "Company X CEO resigns unexpectedly amid fraud probe",
        "Fed surprise: emergency rate cut announced",
        "Major retailer slashes guidance for Q4",
        "Trading halt issued on ABC after liquidity crisis",
    ],
)
def test_classify_severity_high(headline: str) -> None:
    assert classify_severity(headline) == "high"


@pytest.mark.parametrize(
    "headline",
    [
        "MSFT announces $5B AI infrastructure deal",
        "Acquisition of TargetCo confirmed by buyer",
        "Antitrust scrutiny widens for Big Tech",
        "ABC downgraded by Morgan Stanley",
    ],
)
def test_classify_severity_medium(headline: str) -> None:
    assert classify_severity(headline) == "medium"


@pytest.mark.parametrize(
    "headline",
    [
        "Rosenblatt reiterated AAPL with Neutral and target $268",
        "Analyst maintains price target on small cap",
        "Minor article about company website redesign",
    ],
)
def test_classify_severity_low(headline: str) -> None:
    assert classify_severity(headline) == "low"
