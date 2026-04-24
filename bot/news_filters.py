"""Headline cleaning, categorisation, and severity classification.

These helpers are pure (no IO, no globals) so they unit-test trivially
and can be reused from any orchestrator. The bot uses them in two
places:

* :func:`bot.news_report.generate_report` cleans IBKR headlines before
  they ever reach the JSON or Telegram digest.
* The same routines categorise both IBKR and Perplexity items so the
  Telegram message does not get drowned in analyst-rating noise.

Severity classification is deterministic and intentionally
conservative: when in doubt, drop to the lower bucket. The downstream
risk rules can only *tighten* posture from these labels.
"""

from __future__ import annotations

import re
from typing import Iterable, Literal

Category = Literal[
    "major", "analyst", "earnings", "macro", "holdings_risk", "watchlist"
]
Severity = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
# IBKR/Reuters/Briefing/etc. wrap headlines with a leading metadata
# block like {A:800015:L:en:K:n/a:C:0.9775911569595337}. They also
# emit provider tags like [BRFG], [BRFUPDN], [DJN], [TRSI] mid- or
# end-of-line. We strip all of these to produce human-readable text.
_LEADING_METADATA_RE = re.compile(r"^\s*\{[^}]*\}\s*")
_LEADING_BANG_RE = re.compile(r"^\s*!+\s*")
_PROVIDER_TAG_RE = re.compile(r"\s*\[[A-Z][A-Z0-9_+\-]{1,15}\]\s*")
_TRAILING_SYMBOLS_RE = re.compile(
    r"\s+([A-Z]{1,5}\b\.?\s*){1,4}$"
)  # e.g. "... AAPL MSFT" tail emitted by some IBKR feeds
_WS_RE = re.compile(r"\s+")


def clean_ibkr_headline(raw: str) -> str:
    """Return a human-readable headline.

    Drops:
      * leading IBKR metadata braces such as ``{A:800015:L:en:K:n/a:C:0.9...}``
      * leading exclamation marks (``!``)
      * provider bracket tags (``[BRFG]``, ``[BRFUPDN]``, ``[DJN]``,
        ``[TRSI]``, etc.)
      * a trailing run of plain capitalised symbols
      * collapsed whitespace.

    If the input is empty or only metadata, returns an empty string.
    """
    if not raw:
        return ""
    s = _LEADING_METADATA_RE.sub("", raw)
    s = _LEADING_BANG_RE.sub("", s)
    s = _PROVIDER_TAG_RE.sub(" ", s)
    s = _TRAILING_SYMBOLS_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------
def _dedupe_key(headline: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", headline.lower()).strip()


def dedupe_headlines(items: Iterable[dict]) -> list[dict]:
    """Drop items whose cleaned headline collides with an earlier one.

    When duplicates are found the first occurrence is kept. The
    ``symbols`` lists are merged so we don't lose context, and the
    higher severity wins (``high > medium > low``).
    """
    seen: dict[str, dict] = {}
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    for raw in items:
        head = (raw.get("headline") or "").strip()
        if not head:
            continue
        key = _dedupe_key(head)
        if not key:
            continue
        existing = seen.get(key)
        if existing is None:
            seen[key] = dict(raw)
            continue
        merged_symbols = sorted(
            {*(existing.get("symbols") or []), *(raw.get("symbols") or [])}
        )
        existing["symbols"] = merged_symbols
        new_sev = (raw.get("severity") or "low").lower()
        old_sev = (existing.get("severity") or "low").lower()
        if severity_rank.get(new_sev, 0) > severity_rank.get(old_sev, 0):
            existing["severity"] = new_sev
    return list(seen.values())


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------
_ANALYST_KEYWORDS = (
    "reiterated",
    "reiterates",
    " price target ",
    "raises pt",
    "lowers pt",
    "raises price target",
    "lowers price target",
    "upgrade",
    "upgraded",
    "downgrade",
    "downgraded",
    "initiates coverage",
    "initiated coverage",
    "coverage with neutral",
    "coverage with buy",
    "coverage with sell",
    " buy rating",
    " sell rating",
    " hold rating",
    " outperform",
    " underperform",
    " overweight",
    " underweight",
    "rating maintained",
    "estimate raised",
    "estimate lowered",
    "rosenblatt",
    "wedbush",
    "piper sandler",
    "morgan stanley",
    "jefferies",
    "loop capital",
    "raymond james",
)

_EARNINGS_KEYWORDS = (
    "earnings",
    "revenue",
    "guidance",
    "eps ",
    "quarterly results",
    "q1 results",
    "q2 results",
    "q3 results",
    "q4 results",
    "beats consensus",
    "misses consensus",
    "preliminary results",
)

# Macro keywords are matched against ``" word "`` so single tokens like
# "fed", "ecb", "boj" do not falsely match inside other words ("boeing",
# "election" contains "ecti" etc.). Multi-word phrases match anywhere.
_MACRO_KEYWORDS_TOKENS = (
    "fed",
    "fomc",
    "powell",
    "cpi",
    "ppi",
    "nonfarm",
    "payroll",
    "tariff",
    "tariffs",
    "vix",
    "ceasefire",
    "sanction",
    "sanctions",
    "ecb",
    "boj",
    "boe",
    "opec",
    "election",
)
_MACRO_KEYWORDS_PHRASES = (
    "jobless claims",
    "treasury yield",
    "10-year",
    "10y yield",
    "geopolit",
    "central bank",
    "interest rate",
    "rate cut",
    "rate hike",
)


def _haystack(headline: str, summary: str = "") -> str:
    return f" {headline.lower()} {summary.lower()} ".replace(".", " ")


def categorize_headline(
    headline: str,
    summary: str = "",
    catalyst_hint: str = "",
) -> Category:
    """Classify a single news item.

    Order of checks: analyst -> earnings -> macro -> default ``major``.
    The classifier is intentionally conservative; ambiguous items end
    up in ``major`` so the operator still sees them.
    """
    text = _haystack(headline + " " + catalyst_hint, summary)
    if any(kw in text for kw in _ANALYST_KEYWORDS):
        return "analyst"
    if any(kw in text for kw in _EARNINGS_KEYWORDS):
        return "earnings"
    if any(f" {kw} " in text for kw in _MACRO_KEYWORDS_TOKENS):
        return "macro"
    if any(kw in text for kw in _MACRO_KEYWORDS_PHRASES):
        return "macro"
    return "major"


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------
_HIGH_SEVERITY_KEYWORDS = (
    "sec investigation",
    "sec probe",
    "sec subpoena",
    "doj investigation",
    "doj probe",
    "doj subpoena",
    "fraud",
    "bankruptcy",
    "chapter 11",
    "liquidity crisis",
    "going concern",
    "trading halt",
    "halted",
    "guidance cut",
    "guidance withdrawn",
    "slashes guidance",
    "earnings miss",
    "misses estimates",
    "ceo resigns",
    "ceo steps down",
    "ceo fired",
    "ceo terminated",
    "geopolitical shock",
    "tariff shock",
    "war breaks out",
    "missile strike",
    "attack on",
    "fed surprise",
    "emergency rate",
    "shutdown",
    "default ",
    "credit downgrade",
    "sanction",
    "restatement",
    "going-concern",
)

_MEDIUM_SEVERITY_KEYWORDS = (
    "earnings beat",
    "earnings miss",  # caught above as high too; that's fine
    "revenue beat",
    "guidance raised",
    "ai infrastructure deal",
    "data center deal",
    "acquisition",
    "merger",
    "antitrust",
    "premarket gap",
    "premarket plunge",
    "premarket surge",
    "downgrade by ",
    "downgraded by ",
    "upgrade by ",
    "upgraded by ",
    "secondary offering",
    "stock split",
    "dividend cut",
    "dividend raised",
    "buyback announced",
)


def classify_severity(headline: str, summary: str = "") -> Severity:
    text = _haystack(headline, summary)
    if any(kw in text for kw in _HIGH_SEVERITY_KEYWORDS):
        return "high"
    if any(kw in text for kw in _MEDIUM_SEVERITY_KEYWORDS):
        return "medium"
    return "low"


__all__ = [
    "Category",
    "Severity",
    "clean_ibkr_headline",
    "dedupe_headlines",
    "categorize_headline",
    "classify_severity",
]
