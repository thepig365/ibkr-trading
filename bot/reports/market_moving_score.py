"""Transparent market-moving headline score (0–100). Heuristic only — not financial advice."""

from __future__ import annotations

import re
from dataclasses import dataclass

# High-signal phrases (lowercase). Score conservatively: uncertain → lower tier.
_TIER1 = (
    "earnings surprise",
    "sec investigation",
    "doj ",
    "ftc",
    "antitrust",
    "merger",
    "acquisition",
    "bankruptcy",
    "chapter 11",
    "fed rate",
    "fomc",
    "cpi report",
    "ppi ",
    "export ban",
    "sanction",
    "trading halt",
    "halted",
    "ceo resign",
    "cfo resign",
    "cyberattack",
    "lawsuit",
    "antitrust",
    "downgrade",
    "upgrade",
    "guidance cut",
    "guidance raise",
    "ban ",
    "product recall",
    "material weakness",
    "hostile",
    "takeover",
)
_TIER2 = (
    "inflation",
    "unemployment",
    "recession",
    "default",
    "investigation",
    "subpoena",
    "layoff",
    "strike",
    "outage",
    "breach",
    "settlement",
    "fine ",
    "penalty",
)
_MEGA = frozenset(
    {
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
        "SPY", "QQQ", "IWM", "DIA", "AMD", "INTC", "AVGO", "NFLX",
    }
)


@dataclass
class ScoreResult:
    score: int
    matched_terms: list[str]
    is_watch: bool  # 40–69: interesting but below typical Telegram bar


def score_market_moving(
    title: str,
    *,
    symbol: str = "",
    watchlist: frozenset[str] | set[str] | None = None,
) -> ScoreResult:
    t = (title or "").strip()
    if not t:
        return ScoreResult(0, [], False)
    low = t.lower()
    terms: list[str] = []
    s = 0
    for ph in _TIER1:
        if ph in low:
            terms.append(ph.strip())
            s += 22
    for ph in _TIER2:
        if ph in low:
            terms.append(ph.strip())
            s += 12
    # Macro / rate shorthand
    if re.search(r"\b(fed|fomc|cpi|ppi|nfp|jobs report)\b", low):
        s += 15
        terms.append("macro_keyword")
    sym = (symbol or "").strip().upper()
    if sym and watchlist and sym in {x.upper() for x in watchlist}:
        s += 12
        terms.append("watchlist_symbol")
    if sym in _MEGA:
        s += 10
        terms.append("mega_cap")
    # Cap
    s = min(100, s)
    watch = 40 <= s < 70
    return ScoreResult(score=s, matched_terms=sorted(set(terms))[:12], is_watch=watch)
